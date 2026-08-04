"""
main_window.py
主窗口：左侧导航 + 右侧页面堆栈。

页面：
  1. 工况总览：氧气、氢气、两路温度、两路压力
  2. 槽电压监控：16 路槽电压 + 每通道温度
  3. 数字孪生：电解槽 3D 模型、传感器点位、故障预警、运行建议
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .styles import BG_SIDEBAR, BORDER_COLOR, TEXT_MUTED, TEXT_PRIMARY
from .view_digital_twin import DigitalTwinView
from .view_process_overview import ProcessOverviewView
from .view_voltage import VoltageView

_MENU_ITEMS = [
    {"label": "工况总览", "title": "工况总览"},
    {"label": "槽电压监控", "title": "槽电压监控"},
    {"label": "数字孪生", "title": "数字孪生"},
]


class MainWindow(QMainWindow):
    """电解槽多传感器监控系统主窗口。"""

    recording_start_requested = pyqtSignal(str)
    recording_stop_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("电解槽多传感器监控系统 | Shanghai Electric")
        self.resize(1440, 900)
        self.setMinimumSize(1100, 680)

        self._buttons: list[QPushButton] = []
        self._pages: list[QWidget] = []
        self._save_path = ""
        self._build_ui()
        self._build_status_bar()
        self._switch_page(1)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_lay = QHBoxLayout(central)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setMinimumWidth(208)
        sidebar.setMaximumWidth(260)
        sidebar_lay = QVBoxLayout(sidebar)
        sidebar_lay.setContentsMargins(0, 0, 0, 0)
        sidebar_lay.setSpacing(0)

        logo_frame = QFrame()
        logo_frame.setFixedHeight(86)
        logo_frame.setStyleSheet(
            f"background-color: {BG_SIDEBAR}; border-bottom: 1px solid {BORDER_COLOR};"
        )
        logo_lay = QVBoxLayout(logo_frame)
        logo_lay.setContentsMargins(16, 12, 16, 12)
        logo_lay.setSpacing(2)

        sys_name = QLabel("电解槽监控系统")
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(17)
        font.setBold(True)
        sys_name.setFont(font)
        sys_name.setStyleSheet(f"color: {TEXT_PRIMARY};")

        sys_sub = QLabel("MULTI-SENSOR DASHBOARD")
        sys_sub.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px; letter-spacing: 1px;")
        logo_lay.addWidget(sys_name)
        logo_lay.addWidget(sys_sub)
        sidebar_lay.addWidget(logo_frame)

        menu_title = QLabel("监控模块")
        menu_title.setObjectName("sidebar_title")
        menu_title.setFixedHeight(34)
        sidebar_lay.addWidget(menu_title)

        for i, item in enumerate(_MENU_ITEMS):
            btn = QPushButton(item["label"])
            btn.setObjectName("menu_btn")
            btn.setCheckable(False)
            btn.setProperty("active", "false")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setFixedHeight(50)
            btn.clicked.connect(lambda _, n=i: self._switch_page(n))
            sidebar_lay.addWidget(btn)
            self._buttons.append(btn)

        sidebar_lay.addStretch()
        self._build_save_panel(sidebar_lay)

        ver_lbl = QLabel("v1.1.0  Network Ready")
        ver_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ver_lbl.setFixedHeight(34)
        ver_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px;")
        sidebar_lay.addWidget(ver_lbl)

        main_lay.addWidget(sidebar)

        self._stack = QStackedWidget()
        self._overview_page = ProcessOverviewView()
        self._voltage_page = VoltageView()
        self._twin_page = DigitalTwinView()
        self._pages = [self._overview_page, self._voltage_page, self._twin_page]

        for page in self._pages:
            self._stack.addWidget(page)
        main_lay.addWidget(self._stack, stretch=1)

    def _build_save_panel(self, parent_layout: QVBoxLayout):
        panel = QFrame()
        panel.setStyleSheet(
            f"background-color: {BG_SIDEBAR}; border-top: 1px solid {BORDER_COLOR};"
        )
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        title = QLabel("数据保存")
        title.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 12px; font-weight: 700;")
        layout.addWidget(title)

        self._save_path_label = QLabel("尚未选择 Excel 文件")
        self._save_path_label.setWordWrap(True)
        self._save_path_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px;")
        layout.addWidget(self._save_path_label)

        choose_button = QPushButton("选择保存位置")
        choose_button.clicked.connect(self._choose_save_path)
        choose_button.setStyleSheet(self._save_button_style("#0969da"))
        self._choose_save_button = choose_button
        layout.addWidget(choose_button)

        button_row = QHBoxLayout()
        button_row.setSpacing(6)
        self._start_save_button = QPushButton("开始保存")
        self._start_save_button.setEnabled(False)
        self._start_save_button.clicked.connect(self._request_start_recording)
        self._start_save_button.setStyleSheet(self._save_button_style("#1a7f37"))
        button_row.addWidget(self._start_save_button)

        self._stop_save_button = QPushButton("停止保存")
        self._stop_save_button.setEnabled(False)
        self._stop_save_button.clicked.connect(self._request_stop_recording)
        self._stop_save_button.setStyleSheet(self._save_button_style("#cf222e"))
        button_row.addWidget(self._stop_save_button)
        layout.addLayout(button_row)

        self._save_state_label = QLabel("未开始记录")
        self._save_state_label.setWordWrap(True)
        self._save_state_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px;")
        layout.addWidget(self._save_state_label)
        parent_layout.addWidget(panel)

    @staticmethod
    def _save_button_style(color: str) -> str:
        return (
            "QPushButton {"
            f"color: {color}; background: transparent; border: 1px solid {color};"
            "border-radius: 4px; padding: 5px 4px; font-size: 11px;"
            "}"
            f"QPushButton:hover {{ background: {color}22; }}"
            "QPushButton:disabled { color: #6e7781; border-color: #30363d; }"
        )

    def _build_status_bar(self):
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._status_left = QLabel("就绪")
        self._status_right = QLabel("数据源：未连接")
        self._status_right.setStyleSheet(f"color: {TEXT_MUTED};")
        sb.addWidget(self._status_left)
        sb.addPermanentWidget(self._status_right)

    def _switch_page(self, index: int):
        self._stack.setCurrentIndex(index)
        title = _MENU_ITEMS[index]["title"]
        self._status_left.setText(f"当前：{title}")

        for i, btn in enumerate(self._buttons):
            btn.setProperty("active", "true" if i == index else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def update_source_status(self, status: str):
        labels = {
            "mock_running": "数据源：内置模拟",
            "hardware_running": "数据源：网络后端",
            "stopped": "数据源：已停止",
        }
        self._status_right.setText(labels.get(status, f"数据源：{status}"))

    def _choose_save_path(self):
        suggested = Path.cwd() / f"传感器数据_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
        file_path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "选择 Excel 保存位置",
            str(suggested),
            "Excel 工作簿 (*.xlsx)",
        )
        if not file_path:
            return
        path = Path(file_path)
        if path.suffix.lower() != ".xlsx":
            path = path.with_suffix(".xlsx")
        self._save_path = str(path)
        self._save_path_label.setText(path.name)
        self._save_path_label.setToolTip(str(path))
        self._start_save_button.setEnabled(True)
        self._save_state_label.setText("位置已选择，点击“开始保存”")

    def _request_start_recording(self):
        if not self._save_path:
            return
        self._choose_save_button.setEnabled(False)
        self._start_save_button.setEnabled(False)
        self._stop_save_button.setEnabled(False)
        self._save_state_label.setText("正在创建 Excel 文件…")
        self.recording_start_requested.emit(self._save_path)

    def _request_stop_recording(self):
        self._stop_save_button.setEnabled(False)
        self._save_state_label.setText("正在完成保存…")
        self.recording_stop_requested.emit()

    def set_recording_started(self, file_path: str):
        self._save_path = file_path
        self._choose_save_button.setEnabled(False)
        self._start_save_button.setEnabled(False)
        self._stop_save_button.setEnabled(True)
        self._save_state_label.setText("正在记录 | 电压温度 0 行 | 六通道 0 行")
        self.statusBar().showMessage(f"数据正在保存到 {file_path}", 5000)

    def set_recording_stopped(self, file_path: str):
        self._choose_save_button.setEnabled(True)
        self._start_save_button.setEnabled(bool(self._save_path))
        self._stop_save_button.setEnabled(False)
        self._save_state_label.setText("保存已停止，文件已安全写入")
        self.statusBar().showMessage(f"Excel 已保存：{file_path}", 5000)

    def update_recording_progress(self, voltage_rows: int, process_rows: int):
        self._save_state_label.setText(
            f"正在记录 | 电压温度 {voltage_rows} 行 | 六通道 {process_rows} 行"
        )

    def show_recording_error(self, message: str):
        self._choose_save_button.setEnabled(True)
        self._start_save_button.setEnabled(bool(self._save_path))
        self._stop_save_button.setEnabled(False)
        self._save_state_label.setText("保存失败，请重新选择文件")
        QMessageBox.critical(self, "Excel 保存失败", message)

    def update_field_frame(self, frame: dict):
        """兼容旧的场数据帧，并从中抽取总览页和数字孪生页需要的 6 个点。"""
        self._overview_page.update_from_field_frame(frame)
        self._twin_page.update_from_field_frame(frame)

    def update_process(self, data: dict):
        self._overview_page.update_process(data)
        if self._stack.currentWidget() is self._twin_page:
            self._twin_page.update_from_process(data)

    def update_twin(self, data: dict):
        if "sensors" in data:
            self._overview_page.update_process(data)
        self._twin_page.update_twin(data)

    def update_voltage_frame(self, frame: dict):
        self._voltage_page.update_channels(frame)

    def update_voltage(self, data: dict):
        """兼容旧接口：可传平铺通道值，也可传包含 channels 的完整帧。"""
        self._voltage_page.update_channels(data)

    def update_field(self, field_id: int, data: list[dict]):
        """兼容旧接口：旧 3D 场页面已合并为工况总览，因此这里转换为旧场数据帧。"""
        field_names = ["temperature", "h2_concentration", "pressure", "o2_concentration"]
        if 0 <= field_id < len(field_names):
            self.update_field_frame({"fields": {field_names[field_id]: data}})
