import json
from PyQt6.QtCore import QObject,QTimer,QUrl,pyqtSignal,QFile,QIODevice
from PyQt6.QtNetwork import QNetworkAccessManager,QNetworkRequest,QNetworkReply

class ModelClient(QObject):
    result=pyqtSignal(str,object)
    failed=pyqtSignal(str,str)
    def __init__(self,base_url,parent=None):
        super().__init__(parent);self.base_url=base_url.rstrip('/');self.manager=QNetworkAccessManager(self);self.active={};self.timeouts={};self.handlers={};self.closed=False
    def request(self,method,path,payload=None,tag='request'):
        raw=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode() if payload is not None else b''
        return self._send(method,path,raw,'application/json',tag,5000)
    def upload(self,path,file_path,tag='upload'):
        if self.closed:return None
        source=QFile(str(file_path))
        if not source.open(QIODevice.OpenModeFlag.ReadOnly):
            self.failed.emit(tag,'无法打开文件：'+source.errorString());return None
        reply=self._send('POST',path,source,'application/octet-stream',tag,60000)
        if reply is not None:source.setParent(reply)
        else:source.close();source.deleteLater()
        return reply
    def _send(self,method,path,payload,content_type,tag,timeout_ms):
        if self.closed:return None
        req=QNetworkRequest(QUrl(self.base_url+path));req.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader,content_type)
        req.setTransferTimeout(timeout_ms+1000)
        if method=='GET':reply=self.manager.get(req)
        else:reply=self.manager.sendCustomRequest(req,method.encode(),payload)
        expired=[False]
        def abort_timeout():expired[0]=True;reply.abort()
        timeout=QTimer(reply);timeout.setSingleShot(True);timeout.timeout.connect(abort_timeout);timeout.start(timeout_ms)
        self.active[reply]=tag
        self.timeouts[reply]=timeout
        def finish():
            timeout.stop();self.active.pop(reply,None);self.timeouts.pop(reply,None);self.handlers.pop(reply,None)
            raw=bytes(reply.readAll()) if reply.isOpen() else b'';status=reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            try:
                data=json.loads(raw) if raw else {}
                if reply.error()!=QNetworkReply.NetworkError.NoError or not status or int(status)>=400:
                    message=f'请求超时：超过{timeout_ms//1000}秒，已取消；可重试或检查本机后台' if expired[0] else data.get('detail',reply.errorString())
                    self.failed.emit(tag,str(message))
                else:self.result.emit(tag,data)
            except (ValueError,TypeError):self.failed.emit(tag,'后台返回的内容无效')
            finally:reply.deleteLater()
        self.handlers[reply]=finish;reply.finished.connect(finish)
        return reply
    def cancel_predictions(self):
        for reply,tag in list(self.active.items()):
            if tag.startswith('predict:'):reply.abort()

    def shutdown(self):
        if self.closed:return
        self.closed=True
        for reply in list(self.active):
            timer=self.timeouts.pop(reply,None)
            if timer is not None:timer.stop();timer.timeout.disconnect()
            handler=self.handlers.pop(reply,None)
            if handler is not None:reply.finished.disconnect(handler)
            reply.abort();reply.deleteLater()
        self.active.clear()
