from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
import json,sqlite3,threading,time,subprocess,sys
from sensor_demo.config import ROOT
from .datasets import validate_training_partitions

ACTIVE=('queued','validating','training','evaluating')

class JobStore:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.db=self.root/'demo.sqlite3'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL, status TEXT NOT NULL, document TEXT NOT NULL)')
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active_training ON jobs((1)) WHERE status IN ('queued','validating','training','evaluating')")
            db.execute('CREATE TABLE IF NOT EXISTS processed_samples (content_id TEXT PRIMARY KEY, dataset_id TEXT, job_id TEXT, start_time TEXT, end_time TEXT, consumed_at REAL)')
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.db,timeout=15)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            with db:yield db
        finally:db.close()
    def get(self,job_id):
        with self.connect() as db:row=db.execute('SELECT document FROM jobs WHERE id=?',(job_id,)).fetchone()
        if not row:raise KeyError('训练任务不存在')
        return json.loads(row[0])
    def list(self):
        with self.connect() as db:rows=db.execute('SELECT document FROM jobs ORDER BY rowid DESC LIMIT 100').fetchall()
        return [json.loads(r[0]) for r in rows]
    def patch(self,job_id,**values):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT document FROM jobs WHERE id=?',(job_id,)).fetchone()
            if not row:raise KeyError('训练任务不存在')
            d=json.loads(row[0]);d.update(values);d['updated_at']=time.time()
            db.execute('UPDATE jobs SET status=?,document=? WHERE id=?',(d['status'],json.dumps(d,ensure_ascii=False),job_id))
        return d
    def mark_consumed(self,job,dataset):
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO processed_samples VALUES (?,?,?,?,?,?)',(dataset['content_id'],dataset['dataset_id'],job['job_id'],dataset['start_time'],dataset['end_time'],time.time()))
    def was_consumed(self,dataset):
        with self.connect() as db:
            return bool(db.execute('SELECT 1 FROM processed_samples WHERE content_id=? OR (start_time<=? AND end_time>=?)',(dataset['content_id'],dataset['end_time'],dataset['start_time'])).fetchone())

class JobService:
    def __init__(self,registry,models,autostart=True):
        self.registry=registry;self.models=models;self.store=JobStore(registry.root);self.stop=threading.Event();self.processes={};self.thread=None
        # A recent independent worker can finish across a backend restart.
        for d in self.store.list():
            if d['status'] in ACTIVE and (d['status']=='queued' or time.time()-d.get('updated_at',0)>45):
                self.store.patch(d['job_id'],status='failed',error='WORKER_INTERRUPTED：后台或任务进程退出，请检查后重新启动训练；原模型保留。')
        if autostart:
            self.thread=threading.Thread(target=self._loop,name='training-supervisor',daemon=True);self.thread.start()
    def create(self,dataset_id,idempotency_key,epochs=5,source='manual'):
        if not idempotency_key or len(idempotency_key)>100:raise ValueError('训练请求身份无效')
        if epochs not in range(1,6):raise ValueError('Demo训练最多5轮')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT document FROM jobs WHERE idempotency_key=?',(idempotency_key,)).fetchone()
            if row:
                existing=json.loads(row[0])
                if existing['dataset_id']!=dataset_id:raise ValueError('相同请求身份不能用于不同数据')
                return existing
            if source=='auto':
                config=db.execute('SELECT document FROM settings WHERE name=?',('auto_training',)).fetchone()
                if not config or not json.loads(config[0]).get('enabled'):raise ValueError('每日任务已关闭，本次不创建训练')
            d=self.registry.get(dataset_id)
            if d['status']!='ready' or not d.get('normal_attested'):raise ValueError('只能使用已登记的合格正常数据训练')
            validate_training_partitions(d.get('partitions',[]))
            if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','validating','training','evaluating')").fetchone():raise ValueError('已有进行中的训练任务，请等待完成或取消')
            job={'job_id':uuid4().hex,'idempotency_key':idempotency_key,'dataset_id':dataset_id,'content_id':d['content_id'],'status':'queued','source':source,'base_version':self.models.version,'base_path':str(self.models.bundle_path),'base_sha256':self.models.bundle_sha256,'recipe_id':'prctf_demo_finetune_v1','epochs':epochs,'batch_size':4,'window_budget':[64,32,32],'progress':0,'epoch':0,'history':[],'cancel_requested':False,'created_at':time.time(),'updated_at':time.time()}
            db.execute('INSERT INTO jobs VALUES (?,?,?,?)',(job['job_id'],idempotency_key,'queued',json.dumps(job,ensure_ascii=False)))
        return job
    def cancel(self,job_id):
        d=self.store.get(job_id)
        if d['status']=='queued':return self.store.patch(job_id,status='cancelled',cancel_requested=True,message='已取消等待中的训练')
        if d['status'] in ACTIVE:return self.store.patch(job_id,cancel_requested=True,message='正在等待当前训练步骤结束后取消')
        return d
    def _loop(self):
        while not self.stop.is_set():
            try:
                jobs=[j for j in self.store.list() if j['status'] in ACTIVE]
                self.models.busy=bool(jobs)
                for job in jobs:
                    jid=job['job_id']
                    if job['status']=='queued':
                        # Serialize the last forward before starting resource use.
                        with self.models.lock:
                            if self.store.get(jid)['status']!='queued':continue
                            self.store.patch(jid,status='validating',message='正在启动独立训练进程')
                            proc=subprocess.Popen([sys.executable,'-m','demo_server.worker','--root',str(self.registry.root),'--job',jid],cwd=ROOT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                            self.processes[jid]=proc
                    else:
                        proc=self.processes.get(jid)
                        if (proc and proc.poll() is not None) or (not proc and time.time()-job['updated_at']>45):
                            if self.store.get(jid)['status'] in ACTIVE:self.store.patch(jid,status='failed',error='WORKER_INTERRUPTED：训练进程意外退出，旧模型未改变。')
                for jid,proc in list(self.processes.items()):
                    if proc.poll() is not None:self.processes.pop(jid,None)
            except Exception as exc:
                # Surface supervisor errors through durable active job records.
                for job in self.store.list():
                    if job['status'] in ACTIVE:self.store.patch(job['job_id'],status='failed',error='训练调度失败：'+str(exc))
            self.stop.wait(.25)
        self.models.busy=False
    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=2)
