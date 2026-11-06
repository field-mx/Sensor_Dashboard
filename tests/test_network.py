"""Protocol-only timing fixture; not a model acceptance substitute."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import threading,time

def test_timeout_is_clear_and_does_not_wait_for_late_reply(qtbot):
    from sensor_demo.network import ModelClient
    class Delayed(BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(7)
            try:self.send_response(200);self.end_headers();self.wfile.write(b'{}')
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Delayed)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        client=ModelClient(f'http://127.0.0.1:{server.server_port}')
        with qtbot.waitSignal(client.failed,timeout=6500) as signal:client.request('GET','/delay',tag='timing-test')
        assert '超时' in signal.args[1]
    finally:server.shutdown();server.server_close()
