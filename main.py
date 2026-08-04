"""
应用入口。

默认启动 UDP 工况、槽电压 PT100 温度和模拟量监听：
  python main.py

接入 Linux 采集板：
  python main.py --source udp --host 0.0.0.0 --port 9090
  # 槽电压 PT100 温度默认仍监听 UDP 9091
  python main.py --source tcp --host 0.0.0.0 --port 9090
  helloworld git save 2026.7.2
"""
from __future__ import annotations

import argparse
import sys

from PyQt6.QtWidgets import QApplication

from backend.data_manager import DataManager
from backend.excel_recorder import ExcelDataRecorder
from backend.network_receiver import NetworkSensorSource
from frontend.main_window import MainWindow
from frontend.styles import DARK_STYLE

DEFAULT_PROCESS_PORT = 9090
DEFAULT_TEMPERATURE_PORT = 9091
DEFAULT_VOLTAGE_PORT = 9092


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="电解槽多传感器监控系统")
    parser.add_argument("--source", choices=["mock", "udp", "tcp"], default="udp", help="数据源类型")
    parser.add_argument("--host", default="0.0.0.0", help="UDP/TCP 监听地址")
    parser.add_argument("--port", type=int, default=DEFAULT_PROCESS_PORT, help="UDP/TCP 监听端口")
    parser.add_argument(
        "--temperature-port",
        type=int,
        default=DEFAULT_TEMPERATURE_PORT,
        help="槽电压 PT100 UDP 温度监听端口",
    )
    parser.add_argument(
        "--voltage-port",
        type=int,
        default=DEFAULT_VOLTAGE_PORT,
        help="槽电压模拟量 AI UDP 监听端口",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    app = QApplication(sys.argv)
    app.setApplicationName("电解槽多传感器监控系统")
    app.setOrganizationName("Shanghai Electric")
    app.setStyleSheet(DARK_STYLE)

    window = MainWindow()
    data_manager = DataManager()
    excel_recorder = ExcelDataRecorder()

    data_manager.field_data_updated.connect(window.update_field_frame)
    data_manager.voltage_data_updated.connect(window.update_voltage_frame)
    data_manager.process_data_updated.connect(window.update_process)
    data_manager.twin_data_updated.connect(window.update_twin)
    data_manager.source_status_changed.connect(window.update_source_status)
    data_manager.voltage_data_updated.connect(excel_recorder.record_voltage_frame)
    data_manager.process_data_updated.connect(excel_recorder.record_process_frame)

    window.recording_start_requested.connect(excel_recorder.start)
    window.recording_stop_requested.connect(excel_recorder.stop)
    excel_recorder.recording_started.connect(window.set_recording_started)
    excel_recorder.recording_stopped.connect(window.set_recording_stopped)
    excel_recorder.progress_ready.connect(window.update_recording_progress)
    excel_recorder.error_ready.connect(window.show_recording_error)

    network_sources: list[NetworkSensorSource] = []
    if args.source == "mock":
        data_manager.start_mock_source()
    else:
        network_source = NetworkSensorSource(protocol=args.source, host=args.host, port=args.port)
        network_source.error_ready.connect(lambda msg: window.statusBar().showMessage(msg, 5000))
        data_manager.set_hardware_source(network_source)
        network_sources.append(network_source)

        if args.source == "udp" and args.temperature_port != args.port:
            temperature_source = NetworkSensorSource(
                protocol="udp",
                host=args.host,
                port=args.temperature_port,
            )
            temperature_source.voltage_data_ready.connect(data_manager._on_voltage_data)
            temperature_source.error_ready.connect(
                lambda msg: window.statusBar().showMessage(msg, 5000)
            )
            network_sources.append(temperature_source)

        if (
            args.source == "udp"
            and args.voltage_port not in (args.port, args.temperature_port)
        ):
            voltage_source = NetworkSensorSource(
                protocol="udp",
                host=args.host,
                port=args.voltage_port,
            )
            voltage_source.voltage_data_ready.connect(data_manager._on_voltage_data)
            voltage_source.error_ready.connect(
                lambda msg: window.statusBar().showMessage(msg, 5000)
            )
            network_sources.append(voltage_source)

        for source in network_sources:
            source.start()

    def _shutdown():
        for source in network_sources:
            source.stop()
        data_manager.stop()
        excel_recorder.stop()

    app.aboutToQuit.connect(_shutdown)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
