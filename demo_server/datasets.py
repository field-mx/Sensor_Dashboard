"""Read-only Excel admission and durable normal-data registry."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4
import hashlib,json,sqlite3,threading
import numpy as np
from sensor_demo.config import RUNTIME,ROOT
from sensor_demo.data import read_excel

def validate_partition(name,count):
    if name=='评估':
        recipe=json.loads((ROOT/'configs/evaluation-recipe.json').read_text(encoding='utf-8'))
        minimum=max(223,96+int(recipe['fault']['steps']))
        if count<minimum:raise ValueError(f'评估分区只有{count}点；固定故障回归需要至少{minimum}点（96秒历史及{recipe["fault"]["steps"]}秒因果回放）。请提供更长的连续正常数据。')
    elif count<223:raise ValueError(f'{name}分区只有{count}点；每区至少223点（32个完整96→96窗口）。请提供更长的连续1秒正常数据。')

def validate_training_partitions(partitions):
    if len(partitions)!=3:raise ValueError('训练数据分区信息缺失，请重新校验Excel。')
    for name,part in zip(('训练','验证','评估'),partitions):validate_partition(name,int(part.get('points',0)))

def training_profile(data):
    n=len(data.times);bounds=[0,n*6//10,n*8//10,n];partitions=[]
    for name,start,end in zip(('训练','验证','评估'),bounds,bounds[1:]):
        count=end-start;windows=max(0,count-191)
        validate_partition(name,count)
        partitions.append({'name':name,'start':start,'end':end,'points':count,'windows':windows,'first_start':start,'last_start':end-192,'start_time':data.times[start].isoformat(),'end_time':data.times[end-1].isoformat()})
    return {'points':n,'channel_count':len(data.channels),'model_channel_count':len(data.model_channels),'start_time':data.times[0].isoformat(),'end_time':data.times[-1].isoformat(),'sample_period_seconds':1,'partitions':partitions,'mapping':[{'channel':tag,'description':data.descriptions[i],'unit':data.units[i],'model_index':data.model_channels.index(tag) if tag in data.model_channels else None} for i,tag in enumerate(data.channels)],'limitations':'历史数据用于功能展示；公开基线可能已见过这些数据，评估不代表独立泛化能力。'}

def read_training_excel(path):
    return read_excel(path,reject_faults=True)

class DatasetRegistry:
    def __init__(self,root=None):
        self.root=Path(root or RUNTIME/'training');self.root.mkdir(parents=True,exist_ok=True)
        self.db=self.root/'demo.sqlite3';self.lock=threading.RLock();self.futures=[]
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS datasets (id TEXT PRIMARY KEY, status TEXT, content_id TEXT, start_time TEXT, end_time TEXT, document TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS dataset_content ON datasets(content_id)')
            interrupted=db.execute("SELECT document FROM datasets WHERE status='validating'").fetchall()
            for (raw,) in interrupted:
                document=json.loads(raw);document.update(status='rejected',error='后台在校验期间退出，请重新导入。')
                self._put(db,document)
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='excel-admission')
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.db,timeout=15)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            with db:yield db
        finally:db.close()
    def _put(self,db,d):
        db.execute('INSERT OR REPLACE INTO datasets VALUES (?,?,?,?,?,?)',(d['dataset_id'],d['status'],d.get('content_id'),d.get('start_time'),d.get('end_time'),json.dumps(d,ensure_ascii=False)))
    def get(self,dataset_id):
        with self.connect() as db:row=db.execute('SELECT document FROM datasets WHERE id=?',(dataset_id,)).fetchone()
        if not row:raise KeyError('数据集不存在')
        return json.loads(row[0])
    def list(self):
        with self.connect() as db:rows=db.execute('SELECT document FROM datasets ORDER BY rowid DESC LIMIT 100').fetchall()
        return [json.loads(row[0]) for row in rows]
    def submit(self,path,filename):
        dataset_id=uuid4().hex;folder=self.root/'datasets'/dataset_id;folder.mkdir(parents=True)
        target=folder/'source.xlsx';Path(path).replace(target)
        d={'dataset_id':dataset_id,'status':'validating','filename':Path(filename.replace('\\','/')).name,'created_at':datetime.now(timezone.utc).isoformat(),'normal_attested':False,'training_allowed':False}
        with self.lock,self.connect() as db:self._put(db,d)
        self.futures=[f for f in self.futures if not f.done()]
        self.futures.append(self.executor.submit(self._validate,dataset_id))
        return d
    def _validate(self,dataset_id):
        d=self.get(dataset_id);folder=self.root/'datasets'/dataset_id
        try:
            data=read_training_excel(folder/'source.xlsx');d.update(training_profile(data))
            d.update(file_sha256=data.file_sha256,content_id=data.content_id)
            with self.lock,self.connect() as db:
                previous=[json.loads(row[0]) for row in db.execute("SELECT document FROM datasets WHERE id<>? AND status IN ('validated','ready')",(dataset_id,))]
                for other in previous:
                    if other.get('content_id')==data.content_id:
                        d.update(status='duplicate',duplicate_of=other['dataset_id'],error='这些通道和采样区间已登记或已校验；改名或重复回放不算新增数据。');self._put(db,d);return
                    if other['start_time']<=d['end_time'] and d['start_time']<=other['end_time']:
                        raise ValueError('与已登记/已校验数据的源时间区间重叠，无法认定为独立新数据。请选择不重叠的新采样区间。')
                np.savez_compressed(folder/'samples.npz',values=data.model_values,times=np.array([t.isoformat() for t in data.times]),channels=np.array(data.model_channels))
                d.update(status='validated',samples_sha256=hashlib.sha256((folder/'samples.npz').read_bytes()).hexdigest())
                self._put(db,d)
        except Exception as exc:
            d.update(status='rejected',error=str(exc) or type(exc).__name__)
            with self.lock,self.connect() as db:self._put(db,d)
    def register(self,dataset_id,normal_attested):
        if normal_attested is not True:raise ValueError('请确认这是未经人工故障注入的正常原始数据。')
        with self.lock,self.connect() as db:
            d=self.get(dataset_id)
            if d['status']=='ready':return d
            if d['status']!='validated':raise ValueError(d.get('error','只有校验通过的数据才能登记'))
            d.update(status='ready',normal_attested=True,training_allowed=True,registered_at=datetime.now(timezone.utc).isoformat())
            self._put(db,d)
        return d
    def wait_idle(self):
        for future in list(self.futures):future.result(timeout=60)
    def close(self):self.executor.shutdown(wait=True,cancel_futures=False)
