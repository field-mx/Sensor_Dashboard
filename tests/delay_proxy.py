"""Acceptance-only proxy: forwards to the REAL model, delays its reply 7s.
No synthetic forecast or production test route is introduced.
"""
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
import httpx,time

class Proxy(BaseHTTPRequestHandler):
    def forward(self):
        raw=self.rfile.read(int(self.headers.get('Content-Length','0')))
        try:
            result=httpx.request(self.command,'http://127.0.0.1:18000'+self.path,content=raw,headers={'Content-Type':'application/json'},timeout=30)
            if self.command=='POST' and self.path.endswith('/predict'):time.sleep(7)
            self.send_response(result.status_code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(result.content)));self.end_headers();self.wfile.write(result.content)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
    do_GET=forward
    do_POST=forward
    def log_message(self,*args):pass

if __name__=='__main__':ThreadingHTTPServer(('127.0.0.1',18001),Proxy).serve_forever()
