import csv
from datetime import datetime
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QPushButton,QTableWidget,QTableWidgetItem,QHeaderView,QFileDialog,QMessageBox
from .config import RUNTIME

class EventHistoryDialog(QDialog):
    def __init__(self,events_provider,channels,parent=None,preferred_channel=None):
        super().__init__(parent);self.provider=events_provider
        self.setWindowTitle('事件历史 · 当前回放');self.resize(1280,620)
        layout=QVBoxLayout(self);bar=QHBoxLayout();layout.addLayout(bar)
        bar.addWidget(QLabel('测点'));self.channel_filter=QComboBox();self.channel_filter.addItems(['全部测点']+list(channels));bar.addWidget(self.channel_filter)
        self.state_filter=QComboBox();self.state_filter.addItems(['全部状态','活动','已恢复','已结束']);bar.addWidget(self.state_filter)
        self.refresh_button=QPushButton('刷新');self.refresh_button.clicked.connect(self.refresh);bar.addWidget(self.refresh_button)
        self.export_button=QPushButton('导出事件 CSV');self.export_button.clicked.connect(self.export);bar.addWidget(self.export_button);bar.addStretch()
        self.note=QLabel('仅显示当前回放事件，最新在前；重置后清空当前列表，导出文件保留。');layout.addWidget(self.note)
        self.table=QTableWidget(0,9);self.table.setHorizontalHeaderLabels(['测点','偏离开始','报警触发','恢复/结束时间','触发残差','冻结阈值','状态','模型版本','结束原因']);self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents);self.table.horizontalHeader().setStretchLastSection(True);layout.addWidget(self.table)
        self.channel_filter.currentTextChanged.connect(self.refresh);self.state_filter.currentTextChanged.connect(self.refresh)
        self.timer=QTimer(self);self.timer.timeout.connect(self.refresh)
        if preferred_channel in channels:self.channel_filter.setCurrentText(preferred_channel)
        self.refresh()
    def filtered(self):
        channel=self.channel_filter.currentText();state=self.state_filter.currentText()
        states={'活动':'active','已恢复':'recovered','已结束':'ended'}
        return [e for e in reversed(self.provider()) if (channel=='全部测点' or e['channel']==channel) and (state=='全部状态' or e['status']==states[state])]
    def refresh(self,*args):
        events=self.filtered();self.table.setRowCount(len(events))
        for row,event in enumerate(events):
            values=[event['channel'],event.get('start_source_time',str(event['start_seq'])),event.get('source_time',str(event['alarm_seq'])),event.get('recovery_source_time',event.get('end_source_time','—')),f'{event["residual"]:.6g}',f'{event["threshold"]:.6g}',{'active':'活动','recovered':'已恢复','ended':'已结束（模型切换）'}[event['status']],event.get('model_version','—'),event.get('end_reason','—')]
            for col,value in enumerate(values):
                item=QTableWidgetItem(value);item.setToolTip(value);self.table.setItem(row,col,item)
        self.note.setText(f'当前回放 · {len(events)}条匹配事件 · 最新在前 · 连续3点触发 / 5点恢复')
    def export(self):
        folder=RUNTIME/'exports/demo_runs';folder.mkdir(parents=True,exist_ok=True)
        path,_=QFileDialog.getSaveFileName(self,'导出当前筛选事件',str(folder/f'events_{datetime.now():%Y%m%d_%H%M%S}.csv'),'CSV (*.csv)',options=QFileDialog.Option.DontUseNativeDialog)
        if not path:return
        fields=['id','run_id','generation','model_version','channel','start_source_time','source_time','recovery_source_time','end_source_time','end_reason','residual','threshold','status']
        try:
            with open(path,'x',encoding='utf-8-sig',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(self.filtered())
            self.note.setText('已导出：'+path)
        except OSError as exc:QMessageBox.warning(self,'导出失败',str(exc))
    def showEvent(self,event):self.timer.start(1000);super().showEvent(event)
    def closeEvent(self,event):self.timer.stop();event.accept()
