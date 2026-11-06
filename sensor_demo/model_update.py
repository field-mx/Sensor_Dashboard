from pathlib import Path
from urllib.parse import quote
from uuid import uuid4
from PyQt6.QtCore import Qt,QTimer,QMimeData,QUrl,pyqtSignal,QTime
from PyQt6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QPushButton,QFileDialog,QCheckBox,QTableWidget,QTableWidgetItem,QHeaderView,QListWidget,QListWidgetItem,QAbstractItemView,QFrame,QTextEdit,QTabWidget,QWidget,QComboBox,QSpinBox,QProgressBar,QTimeEdit
from .config import DEFAULT_DATA
from .network import ModelClient

class ExcelFiles(QListWidget):
    def __init__(self,parent=None):
        super().__init__(parent);self.setDragEnabled(True);self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection);self.setMaximumHeight(110)
    def set_folder(self,folder):
        self.clear()
        if not folder.is_dir():return
        for path in sorted(folder.glob('*.xlsx'))[:100]:
            item=QListWidgetItem(path.name);item.setData(Qt.ItemDataRole.UserRole,str(path));item.setToolTip(str(path));self.addItem(item)
    def mimeData(self,items):
        mime=QMimeData();mime.setUrls([QUrl.fromLocalFile(i.data(Qt.ItemDataRole.UserRole)) for i in items]);return mime

class ExcelDrop(QFrame):
    file_dropped=pyqtSignal(str)
    def __init__(self):
        super().__init__();self.setAcceptDrops(True);self.setMinimumWidth(240)
        self.setStyleSheet('ExcelDrop {border:2px dashed #0969da;border-radius:8px;background:#eef6ff;}')
        layout=QVBoxLayout(self);label=QLabel('将正常 Excel 拖到这里\n仅支持 .xlsx；源文件只读');label.setAlignment(Qt.AlignmentFlag.AlignCenter);layout.addWidget(label)
    def dragEnterEvent(self,event):
        if event.mimeData().hasUrls():event.acceptProposedAction()
    def dropEvent(self,event):
        urls=event.mimeData().urls()
        if len(urls)==1:self.file_dropped.emit(urls[0].toLocalFile());event.acceptProposedAction()

class ModelUpdateDialog(QDialog):
    def __init__(self,base_url,source_path=None,parent=None):
        super().__init__(parent);self.setWindowTitle('模型更新 · 本机后训练');self.resize(1160,860);self.setMinimumSize(1000,720)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose);self.setAcceptDrops(True)
        self.client=ModelClient(base_url,self);self.client.result.connect(self.on_result);self.client.failed.connect(self.on_error)
        self.dataset_id=None;self.report=None;self.busy=False;self.reusing_dataset=False
        outer=QVBoxLayout(self);self.tabs=QTabWidget();outer.addWidget(self.tabs)
        data_page=QWidget();layout=QVBoxLayout(data_page);self.tabs.addTab(data_page,'训练数据')
        self.current_model=QLabel('正在读取当前模型…');layout.addWidget(self.current_model)
        title=QLabel('导入正常多通道数据');title.setStyleSheet('font-size:20px;color:#0969da');layout.addWidget(title)
        row=QHBoxLayout();layout.addLayout(row)
        self.path_edit=QLineEdit(str(source_path or DEFAULT_DATA));self.path_edit.setAccessibleName('训练Excel文件路径');row.addWidget(self.path_edit,1)
        self.browse_button=QPushButton('选择 Excel');self.browse_button.clicked.connect(self.browse);row.addWidget(self.browse_button)
        self.validate_button=QPushButton('校验此文件');self.validate_button.clicked.connect(lambda:self.import_file(self.path_edit.text()));row.addWidget(self.validate_button)
        dragrow=QHBoxLayout();layout.addLayout(dragrow)
        left=QVBoxLayout();dragrow.addLayout(left,1);left.addWidget(QLabel('当前文件夹中的Excel（可拖到右侧，或双击导入）'))
        self.file_list=ExcelFiles();self.file_list.set_folder(Path(self.path_edit.text()).parent);self.file_list.itemDoubleClicked.connect(lambda item:self.import_file(item.data(Qt.ItemDataRole.UserRole)));left.addWidget(self.file_list)
        self.drop_area=ExcelDrop();self.drop_area.file_dropped.connect(self.import_file);dragrow.addWidget(self.drop_area,1)
        self.status=QLabel('请选择或拖入未经人工故障注入的原始正常数据。');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.summary=QTextEdit();self.summary.setReadOnly(True);self.summary.setMaximumHeight(115);layout.addWidget(self.summary)
        self.partitions=QTableWidget(0,5);self.partitions.setHorizontalHeaderLabels(['时间分区','起始源时间','结束源时间','采样点','完整96→96窗口']);self.partitions.setMaximumHeight(142);self.partitions.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);self.partitions.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers);layout.addWidget(self.partitions)
        self.mapping=QTableWidget(0,4);self.mapping.setHorizontalHeaderLabels(['测点ID','测点名称','原始单位','模型输入顺序']);self.mapping.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);self.mapping.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers);layout.addWidget(self.mapping,1)
        self.normal_check=QCheckBox('确认这是正常原始数据，未添加人工故障；仅用于本机训练与评估。');self.normal_check.toggled.connect(self.update_controls);layout.addWidget(self.normal_check)
        self.auto_start=QCheckBox('登记后自动开始后训练（最多5轮，当前模型保留）');self.auto_start.setChecked(True);layout.addWidget(self.auto_start)
        buttons=QHBoxLayout();layout.addLayout(buttons)
        self.register_button=QPushButton('登记到正常训练池');self.register_button.clicked.connect(self.register);buttons.addWidget(self.register_button)
        self.pool_button=QPushButton('查看已登记数据');self.pool_button.clicked.connect(lambda:self.client.request('GET','/api/v1/datasets',tag='pool'));buttons.addWidget(self.pool_button)
        buttons.addStretch();close=QPushButton('关闭');close.clicked.connect(self.close);buttons.addWidget(close)
        self.timer=QTimer(self);self.timer.setInterval(1000);self.timer.timeout.connect(self.poll)
        self.build_training_tab()
        self.build_versions_tab()
        self.build_auto_tab()
        self.path_edit.textEdited.connect(self.path_edited)
        self.update_controls();self.client.request('GET','/api/v1/model-spec',tag='model');self.refresh_pool();self.refresh_jobs();self.refresh_versions();self.poll_auto()
    def build_auto_tab(self):
        page=QWidget();layout=QVBoxLayout(page);self.tabs.addTab(page,'每日后训练')
        note=QLabel('每天只检查已登记、合格且未处理的正常新数据；没有新数据会跳过，重复回放或改名上传不算新增。\n与手动训练共用同一任务锁；训练产生候选，验证和激活在模型版本页完成。关闭前端后，本机后台保持运行即可继续调度。');note.setWordWrap(True);layout.addWidget(note)
        row=QHBoxLayout();layout.addLayout(row)
        self.auto_enabled=QCheckBox('启用每日自动后训练');row.addWidget(self.auto_enabled)
        row.addWidget(QLabel('Asia/Shanghai 执行时间'));self.auto_time=QTimeEdit(QTime(2,0));self.auto_time.setDisplayFormat('HH:mm:ss');row.addWidget(self.auto_time)
        self.auto_save_button=QPushButton('保存每日设置');self.auto_save_button.clicked.connect(self.save_auto);row.addWidget(self.auto_save_button)
        self.auto_check_button=QPushButton('立即检查并后训练');self.auto_check_button.clicked.connect(lambda:self.client.request('POST','/api/v1/auto-training/check',{},tag='auto_check'));row.addWidget(self.auto_check_button);row.addStretch()
        self.auto_next=QLabel('正在读取下一次执行时间…');layout.addWidget(self.auto_next)
        self.auto_status=QLabel('默认关闭，02:00执行；尚未读取后台设置。');self.auto_status.setWordWrap(True);layout.addWidget(self.auto_status)
        self.auto_runs=QTableWidget(0,6);self.auto_runs.setHorizontalHeaderLabels(['检查时间','触发方式','实际结果','原因 / 状态','数据ID','训练任务ID']);self.auto_runs.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);self.auto_runs.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers);layout.addWidget(self.auto_runs,1)
        self.auto_dirty=False;self.auto_save_pending=False;self.auto_loaded=False;self.auto_connection_error=False;self.auto_enabled.toggled.connect(self.auto_edited);self.auto_time.timeChanged.connect(self.auto_edited)
        self.auto_timer=QTimer(self);self.auto_timer.setInterval(2000);self.auto_timer.timeout.connect(self.poll_auto);self.auto_timer.start()
    def auto_edited(self,*args):self.auto_dirty=True;self.auto_status.setText('设置已修改，点击“保存每日设置”生效。')
    def poll_auto(self):
        if 'autoconfig' not in self.client.active.values():self.client.request('GET','/api/v1/auto-training',tag='autoconfig')
    def save_auto(self):
        self.auto_save_pending=True;self.auto_save_button.setEnabled(False);self.auto_status.setText('正在保存每日设置…')
        self.client.request('PUT','/api/v1/auto-training',{'enabled':self.auto_enabled.isChecked(),'local_time':self.auto_time.time().toString('HH:mm:ss')},tag='auto_save')
    def show_auto(self,data):
        if not self.auto_dirty and not self.auto_save_pending:
            self.auto_enabled.blockSignals(True);self.auto_time.blockSignals(True);self.auto_enabled.setChecked(data['enabled']);self.auto_time.setTime(QTime.fromString(data['local_time'],'HH:mm:ss'));self.auto_enabled.blockSignals(False);self.auto_time.blockSignals(False)
        self.auto_next.setText('下一次执行：'+str(data['next_run_time'] or '已关闭')+' · Asia/Shanghai · 后台需保持运行')
        self.auto_runs.setRowCount(len(data['runs']))
        status={'skipped':'跳过','missed':'错过，未补训','dispatched':'已提交','checking':'检查中','failed':'失败','queued':'排队','validating':'启动校验','training':'真实训练中','trained':'训练完成，待验证','succeeded':'验证通过','rejected':'验证未通过','cancelled':'已取消','evaluating':'候选验证中'}
        for row,r in enumerate(data['runs']):
            fields=[r['at'],'每日调度' if r['source']=='auto' else '手动检查',status.get(r.get('job_status',r['status']),r['status']),r.get('job_message',r['message']),r.get('dataset_id','—'),r.get('job_id','—')]
            for col,value in enumerate(fields):
                item=QTableWidgetItem(str(value));item.setToolTip(str(value));self.auto_runs.setItem(row,col,item)
    def build_versions_tab(self):
        page=QWidget();layout=QVBoxLayout(page);self.tabs.addTab(page,'模型版本')
        self.version_current=QLabel('正在读取活动模型…');layout.addWidget(self.version_current)
        note=QLabel('先验证候选，再激活。总体归一化MAE≤基线1.02倍；关键测点≤1.10倍加预设容差；突高和突低因果预警回归均须通过。\n激活先校验权重、预警阈值并预热，成功后保存活动版本；可回滚到公开基线。');note.setWordWrap(True);layout.addWidget(note)
        row=QHBoxLayout();layout.addLayout(row)
        self.version_choice=QComboBox();self.version_choice.currentIndexChanged.connect(self.show_version);row.addWidget(self.version_choice,1)
        refresh=QPushButton('刷新版本');refresh.clicked.connect(self.refresh_versions);row.addWidget(refresh)
        self.candidate_validate_button=QPushButton('验证候选');self.candidate_validate_button.clicked.connect(self.validate_candidate);row.addWidget(self.candidate_validate_button)
        self.activate_button=QPushButton('激活选中版本');self.activate_button.clicked.connect(self.activate_selected);row.addWidget(self.activate_button)
        self.rollback_button=QPushButton('回滚到公开基线');self.rollback_button.clicked.connect(lambda:self.client.request('POST','/api/v1/models/rollback',{},tag='activate'));row.addWidget(self.rollback_button)
        self.validation_status=QLabel('选择候选可查看冻结门槛、评估结果和拒绝原因。');self.validation_status.setWordWrap(True);layout.addWidget(self.validation_status)
        self.version_table=QTableWidget(0,4);self.version_table.setHorizontalHeaderLabels(['模型版本','状态','来源模型','数据ID']);self.version_table.setMaximumHeight(220);self.version_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);self.version_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers);self.version_table.cellClicked.connect(lambda row,col:self.version_choice.setCurrentIndex(row));layout.addWidget(self.version_table)
        self.version_details=QTextEdit();self.version_details.setReadOnly(True);layout.addWidget(self.version_details,1)
        self.version_records={};self.active_version=None;self.baseline_version=None
    def refresh_versions(self):
        if 'models' not in self.client.active.values():self.client.request('GET','/api/v1/models',tag='models')
    def show_version(self,*args):
        d=self.version_records.get(self.version_choice.currentData())
        if not d:self.candidate_validate_button.setEnabled(False);self.activate_button.setEnabled(False);self.rollback_button.setEnabled(False);return
        states={'baseline':'公开基线','trained':'训练完成、待验证','eligible':'通过，可激活','rejected':'未通过，禁止激活'}
        text=f'版本：{d["version"]}\n状态：{states.get(d["status"],d["status"])}\n权重SHA256：{d["sha256"]}\n'
        report=d.get('validation')
        if report:
            text+=f'\n冻结评估：{len(report["evaluation_starts"])}个96→96秒窗口\n总体归一化MAE：候选{report["overall_candidate"]:.7g} / 基线{report["overall_baseline"]:.7g} / 上限{report["overall_limit"]:.7g}\n'
            for c in report['critical']:text+=f'{c["channel"]}：{c["candidate"]:.7g} ≤ {c["limit"]:.7g}，'+('通过' if c['passed'] else '未通过')+'\n'
            for f in report['faults']:
                event=f.get('event') or {};text+=f'突高/突低 {f["channel"]} {f["amplitude"]:+g}：'+('通过' if f['passed'] else '未通过')+f'；触发序号{event.get("alarm_seq","—")}，恢复序号{event.get("recovery_seq","—")}\n'
            if report['reasons']:text+='\n拒绝原因：'+'；'.join(report['reasons'])+'\n'
            text+='\n'+report['limitations']
        else:text+='\n'+d.get('limitations','训练完成后须通过冻结评估和预警回归，才能激活。')
        if d.get('calibration'):text+='\n本版本预警校准ID：'+d['calibration']['calibration_id']
        self.version_details.setPlainText(text)
        active_job=self.job_report and self.job_report['status'] in ('queued','validating','training','evaluating')
        self.candidate_validate_button.setEnabled(d['version']!=self.baseline_version and not report and not active_job)
        self.activate_button.setEnabled(bool(d.get('eligible') and d['version']!=self.active_version and not active_job))
        self.activate_button.setToolTip('仅通过冻结评估及预警回归的候选可以激活；未验证或未通过时禁止激活。')
        self.rollback_button.setEnabled(bool(self.active_version and self.active_version!=self.baseline_version and not active_job))
    def validate_candidate(self):
        version=self.version_choice.currentData()
        if version:self.validation_status.setText('正在提交冻结候选验证；活动模型保留。');self.client.request('POST','/api/v1/models/'+version+'/validate',{},tag='evaluate')
    def activate_selected(self):
        version=self.version_choice.currentData()
        if version:self.validation_status.setText('正在校验、加载并预热选中版本…');self.client.request('POST','/api/v1/models/'+version+'/activate',{},tag='activate')
    def build_training_tab(self):
        page=QWidget();layout=QVBoxLayout(page);self.tabs.addTab(page,'后训练任务')
        self.training_model=QLabel('正在读取当前模型…');layout.addWidget(self.training_model)
        note=QLabel('独立进程真实微调 · Adam学习率为原训练率的0.1倍（当前1e-5） · 每批4窗\n预算：训练64窗 / 验证32窗 / 评估32窗；均匀选取分区内完整窗口。新模型不保证更好，训练完成后需要验证，当前模型保留。');note.setWordWrap(True);layout.addWidget(note)
        row=QHBoxLayout();layout.addLayout(row);row.addWidget(QLabel('正常数据'))
        self.training_data=QComboBox();self.training_data.setMinimumWidth(420);row.addWidget(self.training_data,1)
        self.refresh_data_button=QPushButton('刷新训练池');self.refresh_data_button.clicked.connect(self.refresh_pool);row.addWidget(self.refresh_data_button)
        row.addWidget(QLabel('轮数'));self.epochs=QSpinBox();self.epochs.setRange(1,5);self.epochs.setValue(5);row.addWidget(self.epochs)
        self.start_button=QPushButton('开始真实后训练');self.start_button.clicked.connect(self.start_training);row.addWidget(self.start_button)
        row=QHBoxLayout();layout.addLayout(row);row.addWidget(QLabel('任务历史'))
        self.job_list=QComboBox();self.job_list.currentIndexChanged.connect(self.select_job);row.addWidget(self.job_list,1)
        self.refresh_jobs_button=QPushButton('刷新任务');self.refresh_jobs_button.clicked.connect(self.refresh_jobs);row.addWidget(self.refresh_jobs_button)
        self.cancel_button=QPushButton('取消训练');self.cancel_button.clicked.connect(self.cancel_training);self.cancel_button.setEnabled(False);row.addWidget(self.cancel_button)
        self.job_status=QLabel('尚未开始训练。可选择已登记数据，或到训练数据页导入正常Excel。');self.job_status.setWordWrap(True);layout.addWidget(self.job_status)
        self.training_progress=QProgressBar();self.training_progress.setRange(0,1000);layout.addWidget(self.training_progress)
        self.loss_label=QLabel('等待真实训练数据');layout.addWidget(self.loss_label)
        self.loss_table=QTableWidget(0,4);self.loss_table.setHorizontalHeaderLabels(['Epoch','训练MSE','验证MSE','实际耗时/秒']);self.loss_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);self.loss_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers);layout.addWidget(self.loss_table,1)
        self.job_details=QTextEdit();self.job_details.setReadOnly(True);self.job_details.setMaximumHeight(200);layout.addWidget(self.job_details)
        self.job_id=None;self.job_report=None;self.pending_key=None;self.pending_auto_dataset=None;self.train_request_pending=False
        self.job_timer=QTimer(self);self.job_timer.setInterval(1000);self.job_timer.timeout.connect(self.poll_job)
    def refresh_pool(self):self.client.request('GET','/api/v1/datasets',tag='pool')
    def refresh_jobs(self):
        if 'jobs' not in self.client.active.values():self.client.request('GET','/api/v1/training-jobs',tag='jobs')
    def select_job(self,*args):
        self.job_id=self.job_list.currentData();self.job_report=None
        if self.job_id:self.job_timer.start();self.poll_job()
    def poll_job(self):
        if self.job_id and 'job' not in self.client.active.values():self.client.request('GET','/api/v1/training-jobs/'+self.job_id,tag='job')
    def start_training(self):
        if not self.start_button.isEnabled() or not self.training_data.currentData():return
        self.pending_key=self.pending_key or uuid4().hex;self.train_request_pending=True;self.update_controls()
        self.job_status.setText('正在提交真实训练任务…');self.tabs.setCurrentIndex(1)
        self.client.request('POST','/api/v1/training-jobs',{'dataset_id':self.training_data.currentData(),'idempotency_key':self.pending_key,'epochs':self.epochs.value()},tag='train')
    def cancel_training(self):
        if self.job_id:self.client.request('POST','/api/v1/training-jobs/'+self.job_id+'/cancel',{},tag='cancel')
    def show_job(self,d):
        previously_active=self.job_report and self.job_report['status'] in ('queued','validating','training','evaluating')
        self.job_report=d
        states={'queued':'排队','validating':'校验并启动','training':'真实训练中','evaluating':'候选验证中','trained':'训练完成，候选待验证','failed':'任务失败','cancelled':'已取消','succeeded':'验证通过','rejected':'候选验证未通过'}
        index=self.job_list.findData(d['job_id'])
        if index>=0:self.job_list.setItemText(index,d['job_id'][:12]+' · '+states.get(d['status'],d['status']))
        self.job_status.setText(states.get(d['status'],d['status'])+' · '+d.get('error',d.get('message','')))
        self.training_progress.setValue(round(d.get('progress',0)*10))
        number=lambda v:f'{v:.6g}' if isinstance(v,(int,float)) else '—'
        self.loss_label.setText(f'Epoch {d.get("epoch",0)}/{d["epochs"]} · 实际批次 {d.get("completed_steps",0)}/{d.get("total_steps","—")} · 训练MSE {number(d.get("train_loss"))} · 验证MSE {number(d.get("validation_loss"))} · 实际耗时 {d.get("elapsed_seconds",0)}秒')
        history=d.get('history',[]);self.loss_table.setRowCount(len(history))
        for row,h in enumerate(history):
            for col,value in enumerate([h['epoch'],f'{h["train_loss"]:.8g}',f'{h["validation_loss"]:.8g}',h['elapsed_seconds']]):self.loss_table.setItem(row,col,QTableWidgetItem(str(value)))
        details=f'任务ID：{d["job_id"]}\n来源模型：{d["base_version"]}\n数据ID：{d["dataset_id"]}\n配方：{d["recipe_id"]}'
        if d.get('candidate'):
            c=d['candidate'];details+=f'\n候选版本：{c["version"]}\nSHA256：{c["sha256"]}\n发生变化的参数张量：{c["changed_parameter_count"]}；最佳验证轮次：{c["best_epoch"]}\n'+('此候选当前已激活。' if getattr(self,'active_version',None)==c['version'] else '此候选尚未激活；当前活动版本见模型版本页。')
        self.job_details.setPlainText(details)
        active=d['status'] in ('queued','validating','training','evaluating');self.cancel_button.setEnabled(active and not d.get('cancel_requested'))
        if not active:self.job_timer.stop()
        if hasattr(self,'version_records'):
            if d['status']=='evaluating':self.validation_status.setText(d.get('message','候选验证中')+f' · 验证实际耗时{d.get("evaluation_elapsed_seconds",0)}秒')
            if previously_active and not active:self.validation_status.setText(d.get('error',d.get('message','任务结束')));self.refresh_versions()
            self.show_version()
        self.update_controls()
    def clear_preview(self):
        self.dataset_id=None;self.report=None;self.reusing_dataset=False;self.timer.stop();self.normal_check.setChecked(False);self.mapping.setRowCount(0);self.partitions.setRowCount(0);self.summary.clear()
    def path_edited(self,*args):
        self.clear_preview();self.status.setText('文件路径已更改，请重新校验此文件。');self.status.setStyleSheet('color:#0969da;');self.update_controls()
    def update_controls(self,*args):
        self.validate_button.setEnabled(not self.busy);self.browse_button.setEnabled(not self.busy);self.file_list.setEnabled(not self.busy)
        self.path_edit.setEnabled(not self.busy);self.drop_area.setEnabled(not self.busy)
        self.register_button.setEnabled(bool(not self.busy and self.report and self.report['status']=='validated' and self.normal_check.isChecked()))
        if hasattr(self,'start_button'):
            active=self.job_report and self.job_report['status'] in ('queued','validating','training','evaluating')
            self.start_button.setEnabled(bool(self.training_data.count() and not self.train_request_pending and not active))
    def browse(self):
        path,_=QFileDialog.getOpenFileName(self,'选择正常训练数据',self.path_edit.text(),'Excel (*.xlsx)',options=QFileDialog.Option.DontUseNativeDialog)
        if path:self.import_file(path)
    def import_file(self,path):
        if self.busy:return
        self.clear_preview();self.status.setStyleSheet('color:#0969da;')
        path=Path(path.strip());self.path_edit.setText(str(path));self.file_list.set_folder(path.parent)
        if path.suffix.lower()!='.xlsx' or not path.is_file():
            self.status.setText('无法导入：请选择存在的 .xlsx 文件。');self.update_controls();return
        if path.stat().st_size>50*1024*1024:
            self.status.setText('无法导入：文件超过50MB限制。');self.update_controls();return
        self.busy=True;self.status.setText('正在上传到本机后台，随后校验真实Excel；不会修改源文件。');self.update_controls()
        self.client.upload('/api/v1/datasets?filename='+quote(path.name),path,tag='upload')
    def poll(self):
        if self.dataset_id and 'dataset' not in self.client.active.values():self.client.request('GET','/api/v1/datasets/'+self.dataset_id,tag='dataset')
    def on_result(self,tag,data):
        if tag in ('autoconfig','auto_save'):
            if tag=='autoconfig' and not self.auto_dirty and (not self.auto_loaded or self.auto_connection_error):self.auto_status.setText('后台设置已读取；修改后点击“保存每日设置”生效。')
            self.auto_loaded=True;self.auto_connection_error=False
            if tag=='auto_save':self.auto_dirty=False;self.auto_save_pending=False;self.auto_save_button.setEnabled(True);self.auto_status.setText('每日设置已保存；关闭前端不影响本机后台执行。')
            self.show_auto(data);return
        if tag=='auto_check':self.auto_status.setText(data['message']);self.poll_auto();self.refresh_jobs();return
        if tag=='model':
            text='当前活动模型：'+data['model_version']+' · 本机CPU · 历史96秒→未来96秒';self.current_model.setText(text);self.training_model.setText(text);return
        if tag=='pool':
            rows=data['datasets'];ready=[r for r in rows if r['status']=='ready']
            self.summary.setPlainText('已登记正常数据：'+str(len(ready))+'份\n'+'\n'.join(f'{r["filename"]} · {r["start_time"]} → {r["end_time"]} · {r["dataset_id"]}' for r in ready))
            previous=self.training_data.currentData();self.training_data.clear()
            for r in ready:self.training_data.addItem(f'{r["filename"]} · {r["points"]:,}点 · {r["dataset_id"][:8]}',r['dataset_id'])
            if previous and self.training_data.findData(previous)>=0:self.training_data.setCurrentIndex(self.training_data.findData(previous))
            self.update_controls()
            if self.pending_auto_dataset:
                index=self.training_data.findData(self.pending_auto_dataset);self.pending_auto_dataset=None
                if index>=0:self.training_data.setCurrentIndex(index);self.start_training()
            return
        if tag=='jobs':
            if any(j['idempotency_key']==self.pending_key for j in data['jobs']):self.pending_key=None
            previous=self.job_id;self.job_list.blockSignals(True);self.job_list.clear()
            for j in data['jobs']:self.job_list.addItem(j['job_id'][:12]+' · '+j['status'],j['job_id'])
            if previous and self.job_list.findData(previous)>=0:self.job_list.setCurrentIndex(self.job_list.findData(previous))
            self.job_list.blockSignals(False);self.select_job();return
        if tag=='models':
            previous=self.version_choice.currentData();self.active_version=data['active_version'];self.baseline_version=data['baseline_version'];self.version_current.setText('当前活动版本：'+self.active_version+' · 重启后保留')
            self.version_records={r['version']:r for r in data['versions']};self.version_choice.blockSignals(True);self.version_choice.clear();self.version_table.setRowCount(len(data['versions']))
            labels={'baseline':'公开基线','trained':'待验证','eligible':'验证通过','rejected':'验证未通过'}
            for row,r in enumerate(data['versions']):
                self.version_choice.addItem(r['version']+' · '+labels.get(r['status'],r['status']),r['version'])
                for col,value in enumerate([r['version'],labels.get(r['status'],r['status']),r.get('base_version','—'),r.get('dataset_id','—')]):self.version_table.setItem(row,col,QTableWidgetItem(value))
            index=self.version_choice.findData(previous)
            if index<0 and len(data['versions'])>1:index=1
            if index>=0:self.version_choice.setCurrentIndex(index)
            self.version_choice.blockSignals(False);self.show_version()
            if self.job_report:self.show_job(self.job_report)
            return
        if tag=='activate':
            self.validation_status.setText('活动版本已切换：'+data['model_version']+'；后续预测使用此版本。');self.refresh_versions();self.client.request('GET','/api/v1/model-spec',tag='model');return
        if tag in ('train','cancel','job','evaluate'):
            if tag=='train':self.pending_key=None;self.train_request_pending=False;self.job_id=data['job_id'];self.refresh_jobs()
            if tag=='evaluate':self.job_id=data['job_id'];self.refresh_jobs()
            if data['job_id']!=self.job_id:return
            self.show_job(data)
            if data['status'] in ('queued','validating','training','evaluating'):self.job_timer.start()
            return
        if tag=='upload':
            self.dataset_id=data['dataset_id'];self.status.setText('正在校验通道、数值、1秒连续性、分区和重复内容…');self.timer.start();self.poll();return
        if tag not in ('dataset','register'):return
        if data['status']=='duplicate' and data.get('duplicate_of'):
            self.reusing_dataset=True;self.dataset_id=data['duplicate_of'];self.status.setText('相同内容此前已校验，正在读取已有登记状态；不重复创建训练数据。');self.client.request('GET','/api/v1/datasets/'+self.dataset_id,tag='dataset');return
        self.report=data
        if data['status']=='validating':return
        self.timer.stop();self.busy=False;self.update_controls()
        if tag=='register' and data['status']=='ready':
            if self.auto_start.isChecked():self.pending_auto_dataset=data['dataset_id']
            self.refresh_pool()
        status=data['status']
        if status in ('rejected','duplicate'):
            self.status.setText(('数据被拒绝：' if status=='rejected' else '重复数据：')+data.get('error','未知原因'));self.status.setStyleSheet('color:#cf222e;')
        else:
            if self.reusing_dataset:message='相同内容已校验，请继续确认正常数据并登记。' if status=='validated' else '此数据已在正常训练池，无需重复登记；可到后训练任务页选择。'
            else:message='校验通过：请确认正常数据并登记。' if status=='validated' else '已登记到正常训练池，可用于后训练；原始文件和回放数据互不修改。'
            self.status.setText(message);self.status.setStyleSheet('color:#1a7f37;')
        if 'points' not in data:return
        self.summary.setPlainText(f'{data["filename"]} · {data["points"]:,}点 · {data["channel_count"]}测点 / {data["model_channel_count"]}模型通道 · 1秒采样\n60%训练 / 20%验证 / 20%评估；窗口不跨分区；原始单位，不重复标准化。\n'+data['limitations']+'\n内容ID：'+data['content_id'])
        self.partitions.setRowCount(len(data['partitions']))
        for row,p in enumerate(data['partitions']):
            for col,value in enumerate([p['name'],p['start_time'],p['end_time'],p['points'],p['windows']]):self.partitions.setItem(row,col,QTableWidgetItem(str(value)))
        self.mapping.setRowCount(len(data['mapping']))
        for row,p in enumerate(data['mapping']):
            for col,value in enumerate([p['channel'],p['description'],p['unit'],'仅显示观测' if p['model_index'] is None else str(p['model_index']+1)]):self.mapping.setItem(row,col,QTableWidgetItem(value))
    def register(self):
        if not self.register_button.isEnabled():return
        self.busy=True;self.update_controls();self.client.request('POST','/api/v1/datasets/'+self.dataset_id+'/register',{'normal_attested':self.normal_check.isChecked()},tag='register')
    def on_error(self,tag,message):
        if tag in ('autoconfig','auto_save','auto_check'):
            self.auto_save_pending=False;self.auto_save_button.setEnabled(True);self.auto_status.setText('每日任务操作失败：'+message)
            if tag=='autoconfig':self.auto_connection_error=True;self.auto_next.setText('后台不可达，无法确认下一次执行；请检查本机后台。')
            return
        if tag=='model':self.current_model.setText('当前模型不可用：'+message);self.training_model.setText('当前模型不可用：'+message);return
        if tag=='pool':self.summary.setPlainText('读取训练池失败：'+message);return
        if tag in ('models','activate','evaluate'):
            self.validation_status.setText('模型操作失败：'+message+'；原活动模型保留。')
            if tag!='models':self.refresh_versions()
            return
        if tag in ('train','cancel','job','jobs'):
            self.train_request_pending=False;self.job_status.setText('训练操作失败：'+message+'；可刷新任务，旧模型保留。');self.update_controls()
            if tag=='train':self.refresh_jobs()
            return
        self.timer.stop();self.busy=False;self.status.setText('本机后台操作失败：'+message+'；可重新校验。');self.status.setStyleSheet('color:#cf222e;');self.update_controls()
    def dragEnterEvent(self,event):
        if event.mimeData().hasUrls():event.acceptProposedAction()
    def dropEvent(self,event):
        urls=event.mimeData().urls()
        if len(urls)==1:self.import_file(urls[0].toLocalFile());event.acceptProposedAction()
    def closeEvent(self,event):self.timer.stop();self.job_timer.stop();self.auto_timer.stop();self.client.shutdown();super().closeEvent(event)
