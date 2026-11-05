#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pc_48ch_adc_dual485_gui.py

AD7616 48通道 ADC + 双 RS485 上位机接收程序

ADC显示：原始计数 / 32768 × 5V / 100Ω × 1000，单位 mA
数据保存：用户选择目录后，每10分钟生成一个Excel文件；停止或退出时补存剩余数据

布局：
    左侧：48个 ADC 波形图
    右侧：16行×2列 RS485 数据表
          第一列：PT100温度，UDP 9091
          第二列：模拟量，UDP 9092

运行：
    python pc_48ch_adc_dual485_gui.py
"""

import socket
import threading
import queue
import csv
import io
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
import tkinter as tk
from tkinter import filedialog, ttk
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


ADC_PORT = 9090
PT100_PORT = 9091
AI_PORT = 9092

ADC_NUM = 3
CH_PER_ADC = 16
ADC_CHANNELS = 48

BYTES_PER_PACKET = 1536
BYTES_PER_ADC_BLOCK = 32
WORDS_PER_ADC_BLOCK = 16
BLOCKS_PER_PACKET = BYTES_PER_PACKET // BYTES_PER_ADC_BLOCK
SAMPLES_PER_PACKET = BLOCKS_PER_PACKET // ADC_NUM

BYTE_ORDER = "<i2"
DISPLAY_SAMPLES = 600
PLOT_UPDATE_MS = 120

ADC_FULL_SCALE = 32768.0
ADC_REFERENCE_VOLTAGE = 5.0
CURRENT_RESISTANCE_OHM = 100.0
AMPERE_TO_MILLIAMPERE = 1000.0
ADC_TO_CURRENT_MA = (
    ADC_REFERENCE_VOLTAGE
    / ADC_FULL_SCALE
    / CURRENT_RESISTANCE_OHM
    * AMPERE_TO_MILLIAMPERE
)

SAVE_INTERVAL_SEC = 10 * 60
EXCEL_MAX_DATA_ROWS = 1_048_575

LOG_DIR = Path("pc_receive_logs")
LOG_DIR.mkdir(exist_ok=True)

stop_event = threading.Event()

adc_queue = queue.Queue(maxsize=300)
text_queue = queue.Queue(maxsize=300)

adc_lock = threading.Lock()
adc_buffer = np.zeros((DISPLAY_SAMPLES, ADC_CHANNELS), dtype=np.float32)
adc_write_idx = 0
adc_filled = False

rs485_lock = threading.Lock()
pt100_values = ["--"] * 16
ai_values = ["--"] * 16
pt100_status = "等待9091数据"
ai_status = "等待9092数据"
pt100_count = 0
ai_count = 0

stats_lock = threading.Lock()
adc_packets = 0
adc_bad_packets = 0
adc_last_time = "--"


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def append_adc_samples(samples_16x48):
    global adc_write_idx, adc_filled
    with adc_lock:
        for i in range(samples_16x48.shape[0]):
            adc_buffer[adc_write_idx, :] = samples_16x48[i, :]
            adc_write_idx += 1
            if adc_write_idx >= DISPLAY_SAMPLES:
                adc_write_idx = 0
                adc_filled = True


def get_adc_display_data():
    with adc_lock:
        if not adc_filled:
            return adc_buffer[:adc_write_idx, :].copy()
        return np.vstack((adc_buffer[adc_write_idx:, :], adc_buffer[:adc_write_idx, :])).copy()


def parse_adc_packet(packet):
    if len(packet) != BYTES_PER_PACKET:
        return None
    arr = np.frombuffer(packet, dtype=np.dtype(BYTE_ORDER))
    if arr.size != BLOCKS_PER_PACKET * WORDS_PER_ADC_BLOCK:
        return None

    blocks = arr.reshape(BLOCKS_PER_PACKET, WORDS_PER_ADC_BLOCK).astype(np.float32)
    adc0 = blocks[0::3, :]
    adc1 = blocks[1::3, :]
    adc2 = blocks[2::3, :]
    raw_values = np.concatenate((adc0, adc1, adc2), axis=1)
    return raw_values * ADC_TO_CURRENT_MA


def parse_csv_style_line(line):
    try:
        row = next(csv.reader(io.StringIO(line)))
    except Exception:
        return {
            "kind": "UNKNOWN", "timestamp": "", "tx": "", "rx": "",
            "status": "PARSE_FAIL", "temp": [], "raw": [], "value": [],
            "line": line,
        }

    result = {
        "kind": row[0].strip() if len(row) > 0 else "",
        "timestamp": row[1].strip() if len(row) > 1 else "",
        "tx": "", "rx": "", "status": "",
        "temp": [], "raw": [], "value": [],
        "line": line,
    }

    i = 2
    while i < len(row):
        key = row[i].strip().upper()
        if key == "TX" and i + 1 < len(row):
            result["tx"] = row[i + 1].strip()
            i += 2
        elif key == "RX" and i + 1 < len(row):
            result["rx"] = row[i + 1].strip()
            i += 2
        elif key == "STATUS" and i + 1 < len(row):
            result["status"] = row[i + 1].strip()
            i += 2
        elif key == "TEMP":
            result["temp"] = [x.strip() for x in row[i + 1:i + 17]]
            i += 17
        elif key == "RAW":
            result["raw"] = [x.strip() for x in row[i + 1:i + 17]]
            i += 17
        elif key == "VALUE":
            result["value"] = [x.strip() for x in row[i + 1:i + 17]]
            i += 17
        else:
            i += 1

    return result


_SAVER_STOP = object()


class TenMinuteExcelSaver:
    """在后台按固定时长把三路数据分段保存为 Excel。"""

    def __init__(self, interval_sec=SAVE_INTERVAL_SEC):
        self.interval_sec = float(interval_sec)
        self._lock = threading.Lock()
        self._queue = queue.Queue()
        self._thread = None
        self._accepting = False
        self._status = "未开始保存"
        self._files_saved = 0
        self._adc_rows_total = 0
        self._measurement_rows_total = 0
        self._last_file = ""

    def start(self, directory):
        save_dir = Path(directory)
        save_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._queue = queue.Queue()
            self._accepting = True
            self._status = "正在记录，首个文件将在10分钟后生成"
            self._files_saved = 0
            self._adc_rows_total = 0
            self._measurement_rows_total = 0
            self._last_file = ""
            self._thread = threading.Thread(
                target=self._writer_loop,
                args=(save_dir, self._queue),
                name="ten-minute-excel-saver",
                daemon=True,
            )
            thread = self._thread
        thread.start()
        return True

    def record_adc(self, timestamp, samples):
        array = np.asarray(samples, dtype=np.float64)
        if array.ndim != 2 or array.shape[1] != ADC_CHANNELS:
            return
        self._enqueue(("adc", timestamp, array.copy()))

    def record_voltage_temperature(self, timestamp, voltages=None, temperatures=None):
        voltage_values = self._normalize_values(voltages)
        temperature_values = self._normalize_values(temperatures)
        if voltage_values is None and temperature_values is None:
            return
        self._enqueue(
            (
                "voltage_temperature",
                timestamp,
                voltage_values or [None] * 16,
                temperature_values or [None] * 16,
            )
        )

    def stop(self, wait=True):
        with self._lock:
            thread = self._thread
            if self._accepting:
                self._accepting = False
                self._status = "正在保存当前未满10分钟的数据…"
                self._queue.put(_SAVER_STOP)
        if wait and thread is not None and thread.is_alive():
            thread.join()

    def snapshot(self):
        with self._lock:
            thread = self._thread
            return {
                "recording": self._accepting,
                "busy": thread is not None and thread.is_alive(),
                "status": self._status,
                "files_saved": self._files_saved,
                "adc_rows": self._adc_rows_total,
                "measurement_rows": self._measurement_rows_total,
                "last_file": self._last_file,
            }

    def _enqueue(self, item):
        with self._lock:
            if not self._accepting:
                return
            target_queue = self._queue
        target_queue.put(item)

    def _writer_loop(self, save_dir, target_queue):
        workbook = None
        adc_sheet = None
        measurement_sheet = None
        segment_start = None
        segment_deadline = None
        adc_rows = 0
        measurement_rows = 0
        failed = False
        try:
            while True:
                timeout = 0.2
                if segment_deadline is not None:
                    timeout = max(0.0, min(timeout, segment_deadline - time.monotonic()))
                try:
                    item = target_queue.get(timeout=timeout)
                except queue.Empty:
                    item = None

                if item is _SAVER_STOP:
                    break

                if item is not None:
                    kind = item[0]
                    incoming_rows = item[2].shape[0] if kind == "adc" else 1
                    current_rows = adc_rows if kind == "adc" else measurement_rows
                    if workbook is not None and current_rows + incoming_rows > EXCEL_MAX_DATA_ROWS:
                        self._finalize_segment(
                            workbook,
                            save_dir,
                            segment_start,
                            adc_rows,
                            measurement_rows,
                        )
                        workbook = None

                    if workbook is None:
                        segment_start = datetime.now()
                        segment_deadline = time.monotonic() + self.interval_sec
                        workbook, adc_sheet, measurement_sheet = self._create_segment_workbook()
                        adc_rows = 0
                        measurement_rows = 0

                    timestamp = item[1]
                    if kind == "adc":
                        for values in item[2]:
                            self._append_data_row(adc_sheet, timestamp, values)
                        adc_rows += incoming_rows
                        with self._lock:
                            self._adc_rows_total += incoming_rows
                    else:
                        self._append_data_row(
                            measurement_sheet,
                            timestamp,
                            [*item[2], *item[3]],
                        )
                        measurement_rows += 1
                        with self._lock:
                            self._measurement_rows_total += 1

                if workbook is not None and time.monotonic() >= segment_deadline:
                    self._finalize_segment(
                        workbook,
                        save_dir,
                        segment_start,
                        adc_rows,
                        measurement_rows,
                    )
                    workbook = None
                    adc_sheet = None
                    measurement_sheet = None
                    segment_start = None
                    segment_deadline = None
                    adc_rows = 0
                    measurement_rows = 0

            if workbook is not None:
                self._finalize_segment(
                    workbook,
                    save_dir,
                    segment_start,
                    adc_rows,
                    measurement_rows,
                )
        except Exception as exc:
            failed = True
            with self._lock:
                self._status = f"Excel保存失败：{exc}"
        finally:
            with self._lock:
                self._accepting = False
                self._thread = None
                if not failed:
                    self._status = f"保存已停止，共生成{self._files_saved}个Excel文件"

    def _finalize_segment(
        self,
        workbook,
        save_dir,
        segment_start,
        adc_rows,
        measurement_rows,
    ):
        segment_end = datetime.now()
        info_sheet = workbook.create_sheet("记录信息", 0)
        info_sheet.column_dimensions["A"].width = 22
        info_sheet.column_dimensions["B"].width = 68
        self._append_header(info_sheet, ["项目", "内容"])
        info_sheet.append(["分段开始时间", segment_start])
        info_sheet.append(["分段结束时间", segment_end])
        info_sheet.append(["ADC电流数据行数", adc_rows])
        info_sheet.append(["电压温度数据行数", measurement_rows])
        info_sheet.append(["分段时长", "10分钟；停止或退出时保存不足10分钟的剩余数据"])
        info_sheet.append(["ADC换算", "电流(mA) = 原始值 / 32768 × 5 / 100Ω × 1000"])

        stem = (
            f"传感器数据_{segment_start:%Y%m%d_%H%M%S}"
            f"_至_{segment_end:%Y%m%d_%H%M%S}"
        )
        output_path = save_dir / f"{stem}.xlsx"
        if output_path.exists():
            output_path = save_dir / f"{stem}_{uuid.uuid4().hex[:8]}.xlsx"
        temp_path = save_dir / f".{output_path.stem}.{uuid.uuid4().hex}.tmp.xlsx"

        with self._lock:
            self._status = f"正在生成 {output_path.name}…"
        try:
            workbook.save(temp_path)
            os.replace(temp_path, output_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

        with self._lock:
            self._files_saved += 1
            self._last_file = str(output_path)
            self._status = f"正在记录，已保存 {output_path.name}"

    @staticmethod
    def _create_segment_workbook():
        workbook = Workbook(write_only=True)
        adc_sheet = workbook.create_sheet("ADC电流")
        measurement_sheet = workbook.create_sheet("电压温度")

        adc_headers = ["时间戳"] + [
            f"ADC{adc}-CH{channel:02d} 电流 (mA)"
            for adc in range(ADC_NUM)
            for channel in range(1, CH_PER_ADC + 1)
        ]
        measurement_headers = (
            ["时间戳"]
            + [f"CH{channel:02d}电压 (V)" for channel in range(1, 17)]
            + [f"CH{channel:02d}温度 (°C)" for channel in range(1, 17)]
        )
        TenMinuteExcelSaver._configure_data_sheet(adc_sheet, len(adc_headers))
        TenMinuteExcelSaver._configure_data_sheet(
            measurement_sheet,
            len(measurement_headers),
        )
        TenMinuteExcelSaver._append_header(adc_sheet, adc_headers)
        TenMinuteExcelSaver._append_header(measurement_sheet, measurement_headers)
        return workbook, adc_sheet, measurement_sheet

    @staticmethod
    def _append_header(sheet, values):
        fill = PatternFill("solid", fgColor="1F4E78")
        cells = []
        for value in values:
            cell = WriteOnlyCell(sheet, value=value)
            cell.font = Font(name="Calibri", bold=True, color="FFFFFF")
            cell.fill = fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cells.append(cell)
        sheet.append(cells)

    @staticmethod
    def _configure_data_sheet(sheet, column_count):
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = f"A1:{get_column_letter(column_count)}1"
        sheet.column_dimensions["A"].width = 24
        for column in range(2, column_count + 1):
            sheet.column_dimensions[get_column_letter(column)].width = 20

    @staticmethod
    def _append_data_row(sheet, timestamp, values):
        timestamp_cell = WriteOnlyCell(sheet, value=timestamp)
        timestamp_cell.number_format = "yyyy-mm-dd hh:mm:ss.000"
        sheet.append([timestamp_cell, *[float(value) if value is not None else None for value in values]])

    @staticmethod
    def _normalize_values(values):
        if not values:
            return None
        normalized = []
        for index in range(16):
            value = values[index] if index < len(values) else None
            try:
                normalized.append(float(value))
            except (TypeError, ValueError):
                normalized.append(None)
        return normalized


def adc_receiver():
    global adc_packets, adc_bad_packets, adc_last_time

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", ADC_PORT))
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
    sock.settimeout(0.5)

    log_path = LOG_DIR / f"adc9090_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"{now_str()} listen UDP {ADC_PORT}, expect {BYTES_PER_PACKET} bytes\n")
        while not stop_event.is_set():
            try:
                packet, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break

            with stats_lock:
                adc_packets += 1
                adc_last_time = now_str()
                if len(packet) != BYTES_PER_PACKET:
                    adc_bad_packets += 1
                    f.write(f"{now_str()} bad length {len(packet)} from {addr}\n")
                    f.flush()
                    continue

            received_at = datetime.now()
            try:
                adc_queue.put_nowait((received_at, packet))
            except queue.Full:
                try:
                    adc_queue.get_nowait()
                    adc_queue.put_nowait((received_at, packet))
                except queue.Empty:
                    pass

    sock.close()


def text_receiver(port, name):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", port))
    sock.settimeout(0.5)

    log_path = LOG_DIR / f"{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"{now_str()} listen UDP {port}\n")
        while not stop_event.is_set():
            try:
                data, addr = sock.recvfrom(8192)
            except socket.timeout:
                continue
            except OSError:
                break

            line = data.decode(errors="replace").strip()
            parsed = parse_csv_style_line(line)
            f.write(f"[{now_str()}] from {addr[0]}:{addr[1]} {line}\n")
            f.flush()

            received_at = datetime.now()
            try:
                text_queue.put_nowait((received_at, name, parsed))
            except queue.Full:
                try:
                    text_queue.get_nowait()
                    text_queue.put_nowait((received_at, name, parsed))
                except queue.Empty:
                    pass

    sock.close()


class ReceiverGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("AD7616 48通道 + 双RS485 上位机")
        self.root.geometry("1700x950")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.lines = []
        self.axes = []
        self.table_items = []
        self.saver = TenMinuteExcelSaver()
        self.save_directory = ""

        self._build_layout()
        self._start_threads()
        self._schedule_update()

    def _build_layout(self):
        main = ttk.Frame(self.root)
        main.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        left = ttk.Frame(main)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        right = ttk.Frame(main, width=360)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(8, 0))
        right.pack_propagate(False)

        self._build_adc_plot(left)
        self._build_485_panel(right)

    def _build_adc_plot(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill=tk.X, pady=(0, 4))

        self.status_var = tk.StringVar(value="9090等待数据；9091等待数据；9092等待数据")
        ttk.Label(top, textvariable=self.status_var).pack(side=tk.LEFT, anchor="w")
        ttk.Button(top, text="清空波形", command=self.clear_adc).pack(side=tk.RIGHT, padx=4)

        fig = Figure(figsize=(13.5, 8.4), dpi=100)
        self.fig = fig
        self.canvas = FigureCanvasTkAgg(fig, master=parent)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        rows, cols = 8, 6
        for i in range(ADC_CHANNELS):
            ax = fig.add_subplot(rows, cols, i + 1)
            line, = ax.plot([], [], linewidth=0.7)
            adc_id = i // CH_PER_ADC
            ch_id = i % CH_PER_ADC + 1
            ax.set_title(f"ADC{adc_id}-CH{ch_id} 电流(mA)", fontsize=8)
            ax.grid(True, linewidth=0.25)
            ax.tick_params(labelsize=6)
            ax.set_xlim(0, DISPLAY_SAMPLES)
            self.axes.append(ax)
            self.lines.append(line)

        fig.subplots_adjust(left=0.04, right=0.99, top=0.96, bottom=0.04,
                            hspace=0.72, wspace=0.34)

    def _build_485_panel(self, parent):
        title = ttk.Label(parent, text="RS485 数据", font=("Microsoft YaHei", 11, "bold"))
        title.pack(anchor="w", pady=(0, 5))

        desc = ttk.Label(
            parent,
            text="9091：温度 PT100 / ttyUL2\n9092：模拟量 AI / ttyUL1",
            justify=tk.LEFT
        )
        desc.pack(anchor="w", pady=(0, 6))

        columns = ("temp", "ai")
        self.table = ttk.Treeview(parent, columns=columns, show="headings", height=16)
        self.table.heading("temp", text="温度结果")
        self.table.heading("ai", text="模拟量结果")
        self.table.column("temp", width=160, anchor="center")
        self.table.column("ai", width=160, anchor="center")
        self.table.pack(fill=tk.X, pady=(0, 8))

        for ch in range(1, 17):
            item = self.table.insert("", "end", values=(f"CH{ch:02d}: --", f"CH{ch:02d}: --"))
            self.table_items.append(item)

        status_frame = ttk.LabelFrame(parent, text="状态")
        status_frame.pack(fill=tk.X, pady=(0, 8))

        self.pt100_status_var = tk.StringVar(value="PT100：等待9091数据")
        self.ai_status_var = tk.StringVar(value="AI：等待9092数据")
        ttk.Label(status_frame, textvariable=self.pt100_status_var, wraplength=330).pack(anchor="w", padx=6, pady=3)
        ttk.Label(status_frame, textvariable=self.ai_status_var, wraplength=330).pack(anchor="w", padx=6, pady=3)

        save_frame = ttk.LabelFrame(parent, text="数据保存（10分钟/文件）")
        save_frame.pack(fill=tk.X, pady=(0, 8))

        self.save_dir_var = tk.StringVar(value="尚未选择保存目录")
        ttk.Label(save_frame, textvariable=self.save_dir_var, wraplength=330).pack(
            anchor="w", padx=6, pady=(4, 2)
        )
        save_button_row = ttk.Frame(save_frame)
        save_button_row.pack(fill=tk.X, padx=6, pady=3)
        self.choose_save_button = ttk.Button(
            save_button_row,
            text="选择目录",
            command=self.choose_save_directory,
        )
        self.choose_save_button.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.save_toggle_button = ttk.Button(
            save_button_row,
            text="开始保存",
            command=self.toggle_saving,
            state=tk.DISABLED,
        )
        self.save_toggle_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        self.save_status_var = tk.StringVar(value="未开始保存")
        ttk.Label(save_frame, textvariable=self.save_status_var, wraplength=330).pack(
            anchor="w", padx=6, pady=(2, 5)
        )

        raw_frame = ttk.LabelFrame(parent, text="最近485原始文本")
        raw_frame.pack(fill=tk.BOTH, expand=True)

        self.raw_text = tk.Text(raw_frame, height=16, wrap=tk.WORD, font=("Consolas", 9))
        raw_scroll = ttk.Scrollbar(raw_frame, orient=tk.VERTICAL, command=self.raw_text.yview)
        self.raw_text.configure(yscrollcommand=raw_scroll.set)
        self.raw_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        raw_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(btn_frame, text="清空485显示", command=self.clear_485).pack(fill=tk.X)

    def _start_threads(self):
        threading.Thread(target=adc_receiver, daemon=True).start()
        threading.Thread(target=text_receiver, args=(PT100_PORT, "PT100"), daemon=True).start()
        threading.Thread(target=text_receiver, args=(AI_PORT, "AI"), daemon=True).start()

    def _schedule_update(self):
        self._process_adc_queue()
        self._process_text_queue()
        self._refresh_plot()
        self._refresh_table()
        self._refresh_status()
        self._refresh_save_panel()
        self.root.after(PLOT_UPDATE_MS, self._schedule_update)

    def _process_adc_queue(self, limit=80):
        processed = 0
        while limit is None or processed < limit:
            try:
                received_at, packet = adc_queue.get_nowait()
            except queue.Empty:
                break
            samples = parse_adc_packet(packet)
            if samples is not None:
                append_adc_samples(samples)
                self.saver.record_adc(received_at, samples)
            processed += 1

    def _process_text_queue(self, limit=80):
        global pt100_values, ai_values, pt100_status, ai_status, pt100_count, ai_count

        processed = 0
        while limit is None or processed < limit:
            try:
                received_at, name, parsed = text_queue.get_nowait()
            except queue.Empty:
                break

            line = parsed.get("line", "")
            status = parsed.get("status", "")
            tx = parsed.get("tx", "")
            rx = parsed.get("rx", "")

            if name == "PT100":
                pt100_count += 1
                pt100_status = status if status else "UNKNOWN"
                vals = parsed.get("temp", [])
                if vals:
                    pt100_values = [(vals[i] if i < len(vals) and vals[i] else "--") for i in range(16)]
                    self.saver.record_voltage_temperature(
                        received_at,
                        temperatures=vals,
                    )
                self._append_raw_text("PT100", status, tx, rx, line)

            elif name == "AI":
                ai_count += 1
                ai_status = status if status else "UNKNOWN"
                vals = parsed.get("value", [])
                if vals:
                    ai_values = [(vals[i] if i < len(vals) and vals[i] else "--") for i in range(16)]
                    self.saver.record_voltage_temperature(
                        received_at,
                        voltages=vals,
                    )
                self._append_raw_text("AI", status, tx, rx, line)

            processed += 1

    def _append_raw_text(self, name, status, tx, rx, line):
        self.raw_text.insert(tk.END, f"\n[{now_str()}] {name}\n")
        self.raw_text.insert(tk.END, f"TX: {tx or 'NONE'}\n")
        self.raw_text.insert(tk.END, f"RX: {rx or 'NONE'}\n")
        self.raw_text.insert(tk.END, f"STATUS: {status or 'UNKNOWN'}\n")
        self.raw_text.insert(tk.END, f"{line}\n")
        self.raw_text.insert(tk.END, "-" * 48 + "\n")
        self.raw_text.see(tk.END)

    def _refresh_plot(self):
        data = get_adc_display_data()
        if data.shape[0] <= 1:
            return

        x = np.arange(data.shape[0])
        for i in range(ADC_CHANNELS):
            y = data[:, i]
            self.lines[i].set_data(x, y)
            self.axes[i].set_xlim(0, max(len(x) - 1, 1))

            ymin = float(np.nanmin(y))
            ymax = float(np.nanmax(y))
            if ymin == ymax:
                padding = max(abs(ymin) * 0.05, 1e-6)
                ymin -= padding
                ymax += padding
            margin = (ymax - ymin) * 0.08
            self.axes[i].set_ylim(ymin - margin, ymax + margin)

        self.canvas.draw_idle()

    def _refresh_table(self):
        temp_snapshot = list(pt100_values)
        ai_snapshot = list(ai_values)
        for ch in range(16):
            temp_text = f"CH{ch + 1:02d}: {temp_snapshot[ch]}"
            ai_text = f"CH{ch + 1:02d}: {ai_snapshot[ch]}"
            self.table.item(self.table_items[ch], values=(temp_text, ai_text))

    def _refresh_status(self):
        with stats_lock:
            adc_info = f"9090 ADC包={adc_packets}，错误包={adc_bad_packets}，最近={adc_last_time}"
        self.status_var.set(
            f"{adc_info}    9091 PT100条数={pt100_count}    9092 AI条数={ai_count}"
        )
        self.pt100_status_var.set(f"PT100状态：{pt100_status}")
        self.ai_status_var.set(f"AI状态：{ai_status}")

    def choose_save_directory(self):
        directory = filedialog.askdirectory(title="选择Excel数据保存目录")
        if not directory:
            return
        self.save_directory = directory
        self.save_dir_var.set(directory)
        self.save_toggle_button.configure(state=tk.NORMAL)

    def toggle_saving(self):
        snapshot = self.saver.snapshot()
        if snapshot["recording"]:
            self.save_status_var.set("正在保存当前剩余数据…")
            self.save_toggle_button.configure(state=tk.DISABLED)
            self.saver.stop(wait=False)
            return
        if snapshot["busy"] or not self.save_directory:
            return
        try:
            started = self.saver.start(self.save_directory)
        except OSError as exc:
            self.save_status_var.set(f"无法使用保存目录：{exc}")
            return
        if started:
            self.choose_save_button.configure(state=tk.DISABLED)
            self.save_toggle_button.configure(text="停止保存", state=tk.NORMAL)

    def _refresh_save_panel(self):
        snapshot = self.saver.snapshot()
        self.save_status_var.set(
            f"{snapshot['status']}\n"
            f"ADC行={snapshot['adc_rows']}，电压温度行={snapshot['measurement_rows']}，"
            f"文件={snapshot['files_saved']}"
        )
        if snapshot["recording"]:
            self.choose_save_button.configure(state=tk.DISABLED)
            self.save_toggle_button.configure(text="停止保存", state=tk.NORMAL)
        elif snapshot["busy"]:
            self.choose_save_button.configure(state=tk.DISABLED)
            self.save_toggle_button.configure(text="正在保存…", state=tk.DISABLED)
        else:
            self.choose_save_button.configure(state=tk.NORMAL)
            self.save_toggle_button.configure(
                text="开始保存",
                state=tk.NORMAL if self.save_directory else tk.DISABLED,
            )

    def clear_adc(self):
        global adc_buffer, adc_write_idx, adc_filled
        with adc_lock:
            adc_buffer[:, :] = 0
            adc_write_idx = 0
            adc_filled = False

    def clear_485(self):
        self.raw_text.delete("1.0", tk.END)

    def on_close(self):
        stop_event.set()
        self.save_status_var.set("正在保存剩余数据并退出…")
        self.root.update_idletasks()
        self._process_adc_queue(limit=None)
        self._process_text_queue(limit=None)
        self.saver.stop(wait=True)
        self.root.destroy()


def main():
    root = tk.Tk()
    ReceiverGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
