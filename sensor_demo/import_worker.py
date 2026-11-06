from PyQt6.QtCore import QThread,pyqtSignal
from .data import read_excel

class ImportWorker(QThread):
    ready=pyqtSignal(object)
    failed=pyqtSignal(str)
    progress=pyqtSignal(int)
    def __init__(self,path,parent=None): super().__init__(parent);self.path=path
    def run(self):
        try:self.ready.emit(read_excel(self.path,self.progress.emit))
        except Exception as exc:self.failed.emit(str(exc))
