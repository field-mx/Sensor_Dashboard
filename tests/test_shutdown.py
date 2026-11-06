from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import threading,time

def test_window_close_stops_pending_network_and_health_timer(qtbot):
    from sensor_demo.app import MainWindow
    class Delayed(BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(2)
            try:self.send_response(200);self.end_headers();self.wfile.write(b'{}')
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Delayed);threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        w=MainWindow();qtbot.addWidget(w)
        w.client.base_url=f'http://127.0.0.1:{server.server_port}'
        w.client.request('GET','/slow',tag='pending-close')
        assert w.client.active
        w.close()
        assert not w.client.active,'Closing the window leaves asynchronous replies and their timers alive'
        assert not w.health_timer.isActive()
    finally:server.shutdown();server.server_close()

def test_closing_model_window_can_reopen_and_main_window_exits(qtbot):
    from PyQt6.QtCore import QCoreApplication,QEvent
    from sensor_demo.app import MainWindow
    w=MainWindow();qtbot.addWidget(w)
    w.show_model_update();first=w.model_update_dialog
    first.close();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    assert w.model_update_dialog is None, 'A closed child must not leave a deleted Qt wrapper in the parent'
    w.show_model_update()
    assert w.model_update_dialog is not first
    w.close()
