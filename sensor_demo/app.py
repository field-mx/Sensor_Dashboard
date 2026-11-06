from __future__ import annotations
import sys
import json
from datetime import datetime
from uuid import uuid4
import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt,QTimer
from PyQt6.QtWidgets import QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QListWidget,QStackedWidget,QFrame,QPushButton,QLineEdit,QFileDialog,QTableWidget,QTableWidgetItem,QHeaderView,QComboBox,QProgressBar,QCheckBox,QDoubleSpinBox,QSpinBox
from .styles import DARK_STYLE
from .config import ROOT,RUNTIME,load_settings,save_settings
from .import_worker import ImportWorker
from .replay import ReplayClock
from .network import ModelClient
from .predictions import PredictionLedger
from .alarms import AlarmEngine,FaultInjector
from .history import EventHistoryDialog
from .twin import TwinPanel
from .presentation import APP_NAME,create_brand_header,create_forecast_legend

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1440,900)
        self.setMinimumSize(1120,720)
        self.settings=load_settings();self.dataset=None;self.import_thread=None
        self.clock=ReplayClock();self.observations=[]
        self.ledger=PredictionLedger();self.predicted_rows=[];self.model_version='';self.model_available=False;self.inflight_tag=None;self.model_spec=None;self.last_prediction_error=''
        self.fault_injector=FaultInjector();self.alarm_engine=None;self.alarm_calibration_id=None;self.history_dialog=None;self.event_archive=[]
        self.client=ModelClient(self.settings['api_url'],self);self.client.result.connect(self.on_service_result);self.client.failed.connect(self.on_service_error)
        self.timer=QTimer(self);self.timer.timeout.connect(self.tick)
        self.setAcceptDrops(True)
        central=QWidget(); self.setCentralWidget(central)
        layout=QHBoxLayout(central); layout.setContentsMargins(0,0,0,0)
        sidebar=QFrame(); sidebar.setObjectName('sidebar'); sidebar.setFixedWidth(205)
        side=QVBoxLayout(sidebar)
        self.brand_header=create_brand_header();side.addWidget(self.brand_header)
        self.nav=QListWidget(); self.nav.addItems(['工况总览','监测与预测','数字孪生'])
        self.nav.setStyleSheet('QListWidget {border:0;background:white;} QListWidget::item {padding:18px 8px;} QListWidget::item:selected {background:#ddedff;color:#0969da;}')
        side.addWidget(self.nav)
        self.model_update_dialog=None
        self.model_update_button=QPushButton('模型更新');self.model_update_button.clicked.connect(self.show_model_update);side.addWidget(self.model_update_button)
        footer=QLabel('历史数据回放\n本机真实预测与训练'); footer.setStyleSheet('color:#57606a;padding:12px;'); side.addWidget(footer)
        layout.addWidget(sidebar)
        content=QWidget();self.content_layout=QVBoxLayout(content);layout.addWidget(content,1)
        toolbar=QHBoxLayout();self.content_layout.addLayout(toolbar)
        self.path_edit=QLineEdit(self.settings['data_path']);self.path_edit.setAccessibleName('Excel文件路径');toolbar.addWidget(self.path_edit,1)
        self.browse_button=QPushButton('选择 Excel');self.browse_button.clicked.connect(self.browse);toolbar.addWidget(self.browse_button)
        self.import_button=QPushButton('导入数据');self.import_button.clicked.connect(lambda:self.load_file(self.path_edit.text()));toolbar.addWidget(self.import_button)
        self.path_edit.returnPressed.connect(lambda:self.load_file(self.path_edit.text()))
        self.dataset_label=QLabel('尚未导入 · 可拖入xlsx文件');self.dataset_label.setWordWrap(True);self.content_layout.addWidget(self.dataset_label)
        playback=QHBoxLayout();self.content_layout.addLayout(playback)
        self.play_button=QPushButton('播放');self.play_button.setEnabled(False);self.play_button.clicked.connect(self.play)
        self.pause_button=QPushButton('暂停');self.pause_button.clicked.connect(self.pause)
        self.reset_button=QPushButton('重置');self.reset_button.clicked.connect(self.reset_replay)
        self.speed_combo=QComboBox();self.speed_combo.addItems(['1×','2×','5×','10×']);self.speed_combo.currentIndexChanged.connect(self.change_speed)
        for w in (self.play_button,self.pause_button,self.reset_button,QLabel('速度'),self.speed_combo):playback.addWidget(w)
        self.progress=QProgressBar();self.progress.setRange(0,100);self.progress.setValue(0);playback.addWidget(self.progress,1)
        self.source_label=QLabel('未开始 · 0点');playback.addWidget(self.source_label)
        service=QHBoxLayout();self.content_layout.addLayout(service)
        self.predict_toggle=QCheckBox('启用本机模型预测');self.predict_toggle.setChecked(True);self.predict_toggle.toggled.connect(self.prediction_toggled);service.addWidget(self.predict_toggle)
        self.api_edit=QLineEdit(self.settings['api_url']);self.api_edit.setMinimumWidth(210);self.api_edit.setMaximumWidth(260);self.api_edit.setAccessibleName('本机后台地址');service.addWidget(self.api_edit)
        self.connect_button=QPushButton('连接后台');self.connect_button.clicked.connect(self.connect_service);service.addWidget(self.connect_button)
        self.service_label=QLabel('正在连接本机后台…');self.service_label.setWordWrap(True);service.addWidget(self.service_label,1)
        self.pages=QStackedWidget();self.content_layout.addWidget(self.pages,1)
        for title,description in [('工况总览','导入历史 Excel 后查看实际测点及数据质量。'),('监测与预测','导入数据并播放；积累96秒历史后预测未来连续96秒。'),('数字孪生','电解槽结构示意；实际测点位置尚未确认时不虚构定位。')]:
            page=QWidget(); body=QVBoxLayout(page)
            heading=QLabel(title); heading.setStyleSheet('font-size:24px;padding:12px;color:#0969da;')
            body.addWidget(heading); body.addWidget(QLabel(description)); body.addStretch()
            self.pages.addWidget(page)
        overview=self.pages.widget(0).layout();overview.takeAt(overview.count()-1)
        self.data_table=QTableWidget(0,8);self.data_table.setHorizontalHeaderLabels(['测点ID','测点名称','分组','单位','观测值','质量','模型','预警状态'])
        self.data_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.data_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.data_table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        overview.addWidget(self.data_table)
        monitor=self.pages.widget(1).layout();monitor.takeAt(monitor.count()-1)
        channel_bar=QHBoxLayout();monitor.addLayout(channel_bar)
        channel_bar.addWidget(QLabel('显示测点'))
        self.channel_combo=QComboBox();self.channel_combo.setMinimumWidth(400);self.channel_combo.currentIndexChanged.connect(self.render);channel_bar.addWidget(self.channel_combo)
        self.channel_info=QLabel('未选择测点');channel_bar.addWidget(self.channel_info,1)
        pg.setConfigOptions(antialias=True,background='w',foreground='#24292f')
        self.plot=pg.PlotWidget();self.plot.showGrid(x=True,y=True,alpha=.2);self.plot.setLabel('bottom','源时间序号','秒')
        self.observed_curve=self.plot.plot([],[],pen=pg.mkPen('#0969da',width=2),name='观测')
        self.aligned_curve=self.plot.plot([],[],pen=pg.mkPen('#1a7f37',width=1.5),name='已到达时刻的预测',connect='finite')
        self.future_curve=self.plot.plot([],[],pen=pg.mkPen('#bf8700',width=2,style=Qt.PenStyle.DashLine),name='未来96秒预测')
        self.forecast_legend=create_forecast_legend();monitor.addWidget(self.forecast_legend)
        monitor.addWidget(self.plot,1)
        self.current_label=QLabel('等待播放');monitor.addWidget(self.current_label)
        faultbar=QHBoxLayout();monitor.insertLayout(3,faultbar)
        faultbar.addWidget(QLabel('人工故障'))
        self.fault_channel=QComboBox();self.fault_channel.setMinimumWidth(160);faultbar.addWidget(self.fault_channel)
        faultbar.addWidget(QLabel('幅度（原始单位）'))
        self.fault_amplitude=QDoubleSpinBox();self.fault_amplitude.setRange(.001,1000000);self.fault_amplitude.setDecimals(3);self.fault_amplitude.setValue(20);faultbar.addWidget(self.fault_amplitude)
        faultbar.addWidget(QLabel('持续/秒'));self.fault_duration=QSpinBox();self.fault_duration.setRange(3,120);self.fault_duration.setValue(12);faultbar.addWidget(self.fault_duration)
        self.inject_high=QPushButton('注入突高');self.inject_high.setEnabled(False);self.inject_high.clicked.connect(self.inject_high_fault);faultbar.addWidget(self.inject_high)
        self.inject_low=QPushButton('注入突低');self.inject_low.setEnabled(False);self.inject_low.clicked.connect(lambda:self.inject_fault(-1));faultbar.addWidget(self.inject_low)
        self.stop_fault_button=QPushButton('停止注入');self.stop_fault_button.setEnabled(False);self.stop_fault_button.clicked.connect(self.stop_fault);faultbar.addWidget(self.stop_fault_button)
        self.fault_label=QLabel('未注入 · 预警器仅使用真实残差');self.fault_label.setWordWrap(True);monitor.insertWidget(4,self.fault_label)
        self.residual_plot=pg.PlotWidget();self.residual_plot.setFixedHeight(110);self.residual_plot.showGrid(x=True,y=True,alpha=.2);self.residual_plot.setLabel('left','绝对残差');self.residual_curve=self.residual_plot.plot([],[],pen=pg.mkPen('#cf222e',width=1.5),connect='finite')
        self.threshold_line=pg.InfiniteLine(angle=0,pen=pg.mkPen('#bf8700',style=Qt.PenStyle.DashLine));self.residual_plot.addItem(self.threshold_line);monitor.addWidget(self.residual_plot)
        self.alarm_label=QLabel('预警不可用：等待模型及正常阈值校准');monitor.addWidget(self.alarm_label)
        self.history_button=QPushButton('查看 / 导出事件历史');self.history_button.clicked.connect(self.show_history);monitor.addWidget(self.history_button)
        self.alarm_table=QTableWidget(0,6);self.alarm_table.setHorizontalHeaderLabels(['触发源时间','测点','触发残差','冻结阈值','状态','原因']);self.alarm_table.setMaximumHeight(110);self.alarm_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers);self.alarm_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);monitor.addWidget(self.alarm_table)
        self.fault_region=pg.LinearRegionItem(values=(0,1),movable=False,brush=pg.mkBrush(207,34,46,25));self.plot.addItem(self.fault_region);self.fault_region.setVisible(False)
        twin_layout=self.pages.widget(2).layout()
        while twin_layout.count():
            item=twin_layout.takeAt(0)
            if item.widget():item.widget().deleteLater()
        self.twin_view=TwinPanel();twin_layout.addWidget(self.twin_view);self.twin_view.selected_tag.connect(self.select_tag)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex); self.nav.setCurrentRow(0)
        self.statusBar().showMessage('就绪 · 历史数据回放 · 尚未导入数据')
        self.health_timer=QTimer(self);self.health_timer.timeout.connect(self.poll_service);self.health_timer.start(2000)
        QTimer.singleShot(0,self.poll_service)

    def browse(self):
        path,_=QFileDialog.getOpenFileName(self,'选择历史Excel',self.path_edit.text(),'Excel (*.xlsx)',options=QFileDialog.Option.DontUseNativeDialog)
        if path:self.path_edit.setText(path);self.load_file(path)

    def show_model_update(self):
        from .model_update import ModelUpdateDialog
        if self.model_update_dialog is None:
            self.model_update_dialog=ModelUpdateDialog(self.client.base_url,self.path_edit.text(),self)
            self.model_update_dialog.finished.connect(lambda _:setattr(self,'model_update_dialog',None))
        self.model_update_dialog.show();self.model_update_dialog.raise_()

    def load_file(self,path):
        if self.import_thread and self.import_thread.isRunning():return
        self.pause()
        self.import_button.setEnabled(False);self.browse_button.setEnabled(False)
        self.dataset_label.setStyleSheet('color:#0969da;');self.dataset_label.setText('正在只读校验Excel，请稍候…')
        self.import_thread=ImportWorker(path,self)
        self.import_thread.progress.connect(lambda n:self.dataset_label.setText(f'正在只读校验：已读取{n:,}个采样点…'))
        self.import_thread.ready.connect(self.loaded);self.import_thread.failed.connect(self.import_failed)
        self.import_thread.finished.connect(lambda:(self.import_button.setEnabled(True),self.browse_button.setEnabled(True)))
        self.import_thread.start()

    def loaded(self,data):
        self.dataset=data;self.settings['data_path']=str(data.path);save_settings(self.settings)
        self.dataset_label.setStyleSheet('color:#1a7f37;')
        self.dataset_label.setText(f'{data.path.name} · {len(data.times):,}个采样点 · {len(data.channels)}个测点 / {len(data.model_channels)}个模型通道 · 1秒采样\n{data.times[0]} → {data.times[-1]} · 原始单位 · SHA256 {data.file_sha256[:16]}…')
        self.data_table.setRowCount(len(data.channels))
        for row,tag in enumerate(data.channels):
            std=float(data.values[:,row].std())
            fields=[tag,data.descriptions[row],data.groups[row],data.units[row],f'{data.values[0,row]:.6g}','低方差' if std<1e-8 else '有效', '参与预测' if tag in data.model_channels else '未纳入模型','待预热' if tag in data.model_channels else '不作判断']
            for col,value in enumerate(fields):self.data_table.setItem(row,col,QTableWidgetItem(value))
        self.nav.setCurrentRow(0);self.statusBar().showMessage('导入完成 · 源Excel只读 · 未开始回放')
        self.clock.load(len(data.times));self.reset_replay()
        self.channel_combo.blockSignals(True);self.channel_combo.clear()
        for tag,name in zip(data.channels,data.descriptions):self.channel_combo.addItem(f'{tag} · {name}',tag)
        self.channel_combo.blockSignals(False);self.render()
        self.fault_channel.clear();self.fault_channel.addItems(data.model_channels);self.fault_channel.setCurrentText('TT010301')
        self.play_button.setEnabled(True);self.progress.setRange(0,len(data.times))

    def play(self):
        if self.dataset is None:return
        self.clock.play();self.timer.start(round(1000/self.clock.speed));self.nav.setCurrentRow(1)
        self.statusBar().showMessage(f'播放中 · {self.clock.speed}× · 历史数据回放')

    def pause(self):
        self.clock.pause();self.timer.stop()
        self.statusBar().showMessage('已暂停 · 源时间停止推进')

    def reset_replay(self):
        self.pause();self.clock.reset();self.observations=[];self.progress.setValue(0)
        self.inflight_tag=None;self.client.cancel_predictions();self.ledger.reset(self.clock.run_id,self.clock.generation,self.model_version);self.predicted_rows=[]
        self.aligned_curve.setData([],[]);self.future_curve.setData([],[])
        self.fault_injector.stop();self.fault_region.setVisible(False);self.residual_curve.setData([],[]);self.alarm_table.setRowCount(0)
        if self.alarm_engine:self.alarm_engine.reset()
        self.event_archive=[]
        self.fault_label.setText('未注入 · 预警器仅使用真实残差');self.inject_high.setEnabled(False)
        self.inject_low.setEnabled(False);self.stop_fault_button.setEnabled(False);self.twin_view.clear()
        if self.history_dialog:self.history_dialog.refresh()
        self.source_label.setText('未开始 · 0点');self.current_label.setText('等待播放');self.observed_curve.setData([],[])
        if self.dataset:
            for row,tag in enumerate(self.dataset.channels):
                self.data_table.setItem(row,4,QTableWidgetItem('—'))
                self.data_table.setItem(row,7,QTableWidgetItem('待预热' if tag in self.dataset.model_channels else '不作判断'))
        self.render_alarms()
        if self.model_available and self.predict_toggle.isChecked():
            self.service_label.setText(f'本机模型已连接 · {self.model_version} · 等待播放，积累96秒后预测')
        self.statusBar().showMessage('已重置 · 新运行代次 · 等待播放')

    def change_speed(self,index):
        self.clock.set_speed((1,2,5,10)[index]);self.timer.setInterval(round(1000/self.clock.speed))
        self.statusBar().showMessage(f'回放速度 {self.clock.speed}× · 源数据采样仍为1秒')

    def tick(self):
        seq=self.clock.advance()
        if seq is None:
            if self.clock.running and self.clock.blocked:self.statusBar().showMessage('等待真实模型返回 · 源时间暂缓 · 界面仍可操作')
            return
        self.predicted_rows.append(self.ledger.for_target(seq) if self.model_available and self.predict_toggle.isChecked() else None)
        self.observations.append(self.fault_injector.apply(seq,self.dataset.values[seq]))
        if self.alarm_engine:
            before=len(self.alarm_engine.events)
            self.alarm_engine.update(seq,self.observations[-1][self.dataset.model_indices],self.predicted_rows[-1])
            for event in self.alarm_engine.events[before:]:
                event.update({'run_id':self.clock.run_id,'generation':self.clock.generation,'model_version':self.model_version,'start_source_time':self.dataset.times[event['start_seq']].isoformat(),'source_time':self.dataset.times[event['alarm_seq']].isoformat()})
                self.record_client_event('alarm',event)
            for event in self.alarm_engine.events:
                if event['status']=='recovered' and not event.get('recovery_logged'):
                    event['recovery_source_time']=self.dataset.times[event['recovery_seq']].isoformat();event['recovery_logged']=True
                    self.record_client_event('alarm_recovered',event)
        self.progress.setValue(seq+1)
        self.source_label.setText(f'{self.dataset.times[seq]} · {seq+1:,}/{self.clock.count:,}')
        for row,value in enumerate(self.observations[-1]):self.data_table.setItem(row,4,QTableWidgetItem(f'{value:.6g}'))
        self.render()
        if self.predict_toggle.isChecked() and self.model_available and len(self.observations)>=96 and self.inflight_tag is None:
            self.request_forecast()
        if not self.clock.running:self.timer.stop();self.statusBar().showMessage('回放结束')

    def render(self,*args):
        if self.dataset is None:return
        index=self.channel_combo.currentIndex()
        if index<0:return
        tag=self.dataset.channels[index]
        self.channel_info.setText(f'{self.dataset.groups[index]} · {self.dataset.units[index]} · '+('模型通道' if tag in self.dataset.model_channels else '未纳入模型，仅显示观测'))
        self.plot.setLabel('left',tag,units=self.dataset.units[index])
        if self.observations:
            start=max(0,len(self.observations)-360)
            self.observed_curve.setData(np.arange(start,len(self.observations)),np.asarray(self.observations[start:])[:,index])
            self.current_label.setText(f'源时间 {self.dataset.times[self.clock.seq]} · {tag} = {self.observations[-1][index]:.6g} {self.dataset.units[index]} · 已积累 {min(len(self.observations),96)}/96 秒历史')
            if tag in self.dataset.model_channels:
                mi=self.dataset.model_channels.index(tag)
                aligned=[float(row[mi]) if row is not None else float('nan') for row in self.predicted_rows[start:]]
                self.aligned_curve.setData(np.arange(start,len(self.predicted_rows)),aligned)
                future=self.ledger.latest
                if future and self.model_available and self.predict_toggle.isChecked():self.future_curve.setData(future['target_seqs'],np.asarray(future['predictions'])[:,mi])
                else:self.future_curve.setData([],[])
            else:self.aligned_curve.setData([],[]);self.future_curve.setData([],[])
        self.render_alarms()
        self.refresh_alarm_states()

    def refresh_alarm_states(self):
        if not self.dataset:return
        if self.alarm_engine:
            for tag,state in zip(self.alarm_engine.channels,self.alarm_engine.statuses):
                if tag in self.dataset.channels:self.data_table.setItem(self.dataset.channels.index(tag),7,QTableWidgetItem({'normal':'正常','alarm':'预警','warning':'超限待确认','unavailable':'不可用'}[state]))
        if self.observations:self.twin_view.update_frame(self.dataset,self.observations[-1],self.alarm_engine,self.clock.seq)

    def inject_high_fault(self):
        self.inject_fault(1)

    def inject_fault(self,direction):
        if not self.dataset or not self.alarm_engine or not self.model_available or len(self.observations)<96:return
        tag=self.fault_channel.currentText();start=self.clock.seq+1;duration=self.fault_duration.value()
        amplitude=self.fault_amplitude.value()*direction
        self.fault_injector.start(self.dataset.channels.index(tag),amplitude,duration,start)
        self.channel_combo.setCurrentIndex(self.dataset.channels.index(tag));self.fault_region.setRegion((start,start+duration));self.fault_region.setVisible(True)
        self.record_client_event('fault_injected',{'channel':tag,'amplitude':amplitude,'duration':duration,'start_seq':start,'run_id':self.clock.run_id,'generation':self.clock.generation})
        self.play()

    def stop_fault(self):
        f=self.fault_injector.fault
        if f:
            end=self.clock.seq+1;self.fault_region.setRegion((f['start_seq'],max(f['start_seq'],end)))
            self.record_client_event('fault_stopped',{'channel':self.dataset.channels[f['channel']],'stopped_seq':self.clock.seq,'run_id':self.clock.run_id,'generation':self.clock.generation})
        self.fault_injector.stop();self.fault_label.setText('已停止人工注入 · 后续观测恢复原始数据 · 等待残差恢复')
        self.render_alarms()

    def show_history(self):
        channels=self.dataset.model_channels if self.dataset else []
        if self.history_dialog is None:self.history_dialog=EventHistoryDialog(self.event_history,channels,self,self.fault_channel.currentText())
        self.history_dialog.refresh();self.history_dialog.show();self.history_dialog.raise_()

    def event_history(self):return self.event_archive+(self.alarm_engine.events if self.alarm_engine else [])

    def archive_model_events(self):
        if not self.alarm_engine:return
        source_time=self.dataset.times[self.clock.seq].isoformat() if self.dataset and self.clock.seq>=0 else '—'
        for previous in self.alarm_engine.events:
            event=dict(previous)
            if event['status']=='active':
                event.update(status='ended',end_reason='模型版本或预警校准改变，旧版本判断结束',end_source_time=source_time)
                self.record_client_event('alarm_ended_model_change',event)
            self.event_archive.append(event)

    def select_tag(self,tag):
        if self.dataset and tag in self.dataset.channels:self.channel_combo.setCurrentIndex(self.dataset.channels.index(tag));self.nav.setCurrentRow(1)

    def render_alarms(self):
        engine=self.alarm_engine
        events=self.event_history()
        ready=bool(self.dataset is not None and engine is not None and self.model_available and self.predict_toggle.isChecked() and len(self.observations)>=96)
        self.inject_high.setEnabled(ready and not self.fault_injector.active_at(self.clock.seq+1))
        self.inject_low.setEnabled(self.inject_high.isEnabled());self.stop_fault_button.setEnabled(self.fault_injector.active_at(self.clock.seq+1))
        f=self.fault_injector.fault
        if f:
            remaining=max(0,f['start_seq']+f['duration']-self.clock.seq-1)
            self.fault_label.setText(f'人工注入 · {self.dataset.channels[f["channel"]]} {f["amplitude"]:+g}原始单位 · 剩余{remaining}秒' if remaining else '人工注入已结束 · 等待残差判定；原Excel未修改')
        if engine is None:self.alarm_label.setText('预警不可用：尚未加载匹配模型的正常阈值校准');return
        if not ready or engine.residuals is None:
            self.alarm_label.setText(f'预警判断不可用或仍在预热 · 已有事件{len(events)}条');self.alarm_label.setStyleSheet('color:#57606a;')
        else:
            active=len(engine.active);pending=sum(x=='warning' for x in engine.statuses)
            self.alarm_label.setText(f'活动预警 {active} · 超限待确认 {pending} · 连续3点触发 / 5点恢复 · 预测偏离不等同设备故障诊断')
            self.alarm_label.setToolTip('正常阈值校准ID：'+self.model_spec['alarm_calibration']['calibration_id'])
            self.alarm_label.setStyleSheet('color:#cf222e;' if active else 'color:#bf8700;' if pending else 'color:#1a7f37;')
        self.alarm_table.setRowCount(len(events))
        for row,event in enumerate(reversed(events)):
            status='活动' if event['status']=='active' else '已结束（模型切换）' if event['status']=='ended' else '已恢复 '+event.get('recovery_source_time','').split('T')[-1]
            fields=[event.get('source_time',str(event['alarm_seq'])),event['channel'],f'{event["residual"]:.5g}',f'{event["threshold"]:.5g}',status,event.get('end_reason','连续3点预测残差超阈值')]
            for col,value in enumerate(fields):self.alarm_table.setItem(row,col,QTableWidgetItem(value))
        index=self.channel_combo.currentIndex()
        if self.dataset is not None and index>=0 and self.dataset.channels[index] in self.dataset.model_channels and self.observations:
            mi=self.dataset.model_channels.index(self.dataset.channels[index]);start=max(0,len(self.observations)-360)
            residuals=[abs(obs[index]-pred[mi]) if pred is not None else float('nan') for obs,pred in zip(self.observations[start:],self.predicted_rows[start:])]
            self.residual_curve.setData(np.arange(start,len(self.observations)),residuals);self.threshold_line.setValue(engine.thresholds[mi])
        else:self.residual_curve.setData([],[])

    def connect_service(self):
        from urllib.parse import urlparse
        url=self.api_edit.text().strip().rstrip('/')
        if urlparse(url).hostname not in ('127.0.0.1','localhost'):
            self.service_label.setText('仅支持甲方本机后台：请填写http://127.0.0.1:端口');return
        self.settings['api_url']=url;save_settings(self.settings);self.client.base_url=url
        self.model_available=False;self.invalidate_forecasts();self.poll_service()

    def poll_service(self):
        if self.client.closed:return
        if 'health' not in self.client.active.values():self.client.request('GET','/api/v1/health',tag='health')

    def invalidate_forecasts(self):
        self.inflight_tag=None;self.client.cancel_predictions();self.clock.blocked=False
        self.ledger.reset(self.clock.run_id,self.clock.generation,self.model_version)
        self.future_curve.setData([],[])
        if self.alarm_engine:
            self.alarm_engine.statuses=['unavailable']*len(self.alarm_engine.channels);self.alarm_engine.residuals=None;self.alarm_engine.high[:]=0;self.alarm_engine.low[:]=0
        self.render_alarms()
        self.refresh_alarm_states()

    def prediction_toggled(self,enabled):
        self.invalidate_forecasts()
        if not enabled:self.service_label.setText('预测已关闭 · 仅回放观测，不作残差判断')
        else:self.poll_service()

    def on_service_result(self,tag,data):
        if tag=='health':
            if not data.get('ready'):
                self.model_available=False;self.invalidate_forecasts();self.service_label.setText('训练/验证中，预测暂停' if data.get('training') else '后台尚未就绪');return
            if data.get('model_version')!=self.model_version or self.model_spec is None or not self.model_available:
                if 'spec' not in self.client.active.values():self.client.request('GET','/api/v1/model-spec',tag='spec')
        elif tag=='spec':
            expected=json.loads((ROOT/'configs/model-schema.json').read_text(encoding='utf-8'))['model_channels']
            if data.get('input_channel_ids')!=expected or data.get('prediction_length')!=96 or data.get('input_length')!=96:
                self.model_available=False;self.service_label.setText('模型契约不匹配，拒绝预测');return
            if self.model_version and data['model_version']!=self.model_version:self.clock.generation+=1
            self.model_spec=data;self.model_version=data['model_version'];self.invalidate_forecasts();self.model_available=True
            calibration=data.get('alarm_calibration')
            if calibration and calibration.get('channels')==expected and calibration.get('model_version')==self.model_version:
                if self.alarm_engine is None or self.alarm_calibration_id!=calibration['calibration_id']:
                    self.archive_model_events()
                    self.alarm_engine=AlarmEngine(expected,calibration['thresholds']);self.alarm_calibration_id=calibration['calibration_id']
            else:self.archive_model_events();self.alarm_engine=None;self.alarm_calibration_id=None
            self.render_alarms()
            message=f'本机模型已连接 · {self.model_version} · CPU · 96→96秒 / 56通道'
            if self.last_prediction_error:message='本机连接已恢复，等待有效预测 · 最近异常：'+self.last_prediction_error[:100]
            if not self.predict_toggle.isChecked():message+=' · 预测已关闭'
            self.service_label.setText(message)
        elif tag.startswith('predict:'):
            if tag!=self.inflight_tag:return
            if data.get('request_id')!=tag.split(':',1)[1]:self.on_service_error(tag,'预测请求ID不匹配');return
            try:accepted=self.ledger.add(data)
            except ValueError as exc:self.on_service_error(tag,str(exc));return
            self.inflight_tag=None;self.clock.blocked=False
            if accepted:
                self.last_prediction_error=''
                self.service_label.setText(f'真实本机预测 · {self.model_version} · {data["latency_ms"]:.1f}ms · 未来96点')
                self.record_client_event('forecast',{'request_id':data['request_id'],'generation':data['generation'],'cutoff_seq':data['cutoff_seq'],'target_first':data['target_seqs'][0],'target_last':data['target_seqs'][-1],'version':data['model_version'],'latency_ms':data['latency_ms']})
                self.render()

    def on_service_error(self,tag,message):
        if tag.startswith('predict:') and tag!=self.inflight_tag:return
        if tag.startswith('predict:'):self.inflight_tag=None;self.clock.blocked=False
        self.model_available=False;self.invalidate_forecasts()
        if message!=self.last_prediction_error:self.record_client_event('connection_error',{'tag':tag,'message':message[:240]})
        self.last_prediction_error=message
        self.service_label.setText('预测不可用：'+message[:180]+' · 可继续纯数据回放')

    def record_client_event(self,kind,data):
        try:
            folder=RUNTIME;folder.mkdir(parents=True,exist_ok=True)
            with (folder/'client-events.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps({'at':datetime.now().isoformat(),'kind':kind,**data},ensure_ascii=False)+'\n')
        except OSError:pass

    def request_forecast(self):
        seq=self.clock.seq;request_id=str(uuid4());tag='predict:'+request_id
        payload={'run_id':self.clock.run_id,'generation':self.clock.generation,'request_id':request_id,'model_version':self.model_version,'schema_id':self.model_spec['schema_id'],'cutoff_seq':seq,'source_times':[t.isoformat() for t in self.dataset.times[seq-95:seq+1]],'channel_ids':self.dataset.model_channels,'history':np.asarray(self.observations[-96:])[:,self.dataset.model_indices].tolist()}
        self.inflight_tag=tag;self.clock.blocked=True
        self.service_label.setText('正在真实预测 · 源时间暂缓'+(' · 最近异常：'+self.last_prediction_error[:90] if self.last_prediction_error else ''))
        self.client.request('POST','/api/v1/predict',payload,tag)

    def import_failed(self,message):
        self.dataset_label.setStyleSheet('color:#cf222e;');self.dataset_label.setText('导入失败：'+message)
        self.statusBar().showMessage('导入失败；已有数据保持不变')

    def dragEnterEvent(self,event):
        if event.mimeData().hasUrls():event.acceptProposedAction()

    def dropEvent(self,event):
        urls=event.mimeData().urls()
        if len(urls)==1:
            path=urls[0].toLocalFile();self.path_edit.setText(path);self.load_file(path);event.acceptProposedAction()

    def closeEvent(self,event):
        if self.import_thread and self.import_thread.isRunning():
            self.statusBar().showMessage('正在校验数据，完成后可关闭');event.ignore();return
        self.health_timer.stop();self.timer.stop();self.clock.pause();self.client.shutdown()
        if self.history_dialog:self.history_dialog.close()
        if self.model_update_dialog:self.model_update_dialog.close()
        event.accept()

def main():
    app=QApplication(sys.argv); app.setApplicationName(APP_NAME)
    app.setStyleSheet(DARK_STYLE)
    window=MainWindow(); window.show()
    return app.exec()
