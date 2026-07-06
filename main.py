"""
应用入口。

默认启动 UDP 温度监听：
  python main.py

接入 Linux 采集板：
  python main.py --source udp --host 0.0.0.0 --port 9091
  python main.py --source tcp --host 0.0.0.0 --port 9091
  helloworld git save 2026.7.2
"""
from __future__ import annotations

import argparse
import sys

from PyQt6.QtWidgets import QApplication

from backend.data_manager import DataManager
from backend.network_receiver import NetworkSensorSource
from frontend.main_window import MainWindow
from frontend.styles import DARK_STYLE


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="电解槽多传感器监控系统")
    parser.add_argument("--source", choices=["mock", "udp", "tcp"], default="udp", help="数据源类型")
    parser.add_argument("--host", default="0.0.0.0", help="UDP/TCP 监听地址")
    parser.add_argument("--port", type=int, default=9091, help="UDP/TCP 监听端口")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    app = QApplication(sys.argv)
    app.setApplicationName("电解槽多传感器监控系统")
    app.setOrganizationName("Shanghai Electric")
    app.setStyleSheet(DARK_STYLE)

    window = MainWindow()
    data_manager = DataManager()

    data_manager.field_data_updated.connect(window.update_field_frame)
    data_manager.voltage_data_updated.connect(window.update_voltage_frame)
    data_manager.process_data_updated.connect(window.update_process)
    data_manager.twin_data_updated.connect(window.update_twin)
    data_manager.source_status_changed.connect(window.update_source_status)

    network_source: NetworkSensorSource | None = None
    if args.source == "mock":
        data_manager.start_mock_source()
    else:
        network_source = NetworkSensorSource(protocol=args.source, host=args.host, port=args.port)
        network_source.error_ready.connect(lambda msg: window.statusBar().showMessage(msg, 5000))
        data_manager.set_hardware_source(network_source)
        network_source.start()

    def _shutdown():
        if network_source is not None:
            network_source.stop()
        data_manager.stop()

    app.aboutToQuit.connect(_shutdown)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
