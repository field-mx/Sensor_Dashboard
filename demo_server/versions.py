from pathlib import Path
import hashlib,json,subprocess,sys,time
import numpy as np
from filelock import FileLock
from sensor_demo.config import ROOT
from .model import MODEL_ROOT,BASELINE
from .jobs import ACTIVE
from .bundles import load_bundle_model

def predictor_from_bundle(path,sha):
    try:
        bundle,model=load_bundle_model(path,sha)
        from scripts.pretrained import PRCTFPredictor
        predictor=PRCTFPredictor(model,{k:v for k,v in bundle.items() if k!='state_dict'},'cpu')
        output=predictor.predict(np.tile(predictor.mean.detach().numpy(),(96,1)),channels=predictor.channels)
        if output.shape!=(96,56) or not np.isfinite(output).all():raise ValueError('模型预热输出契约无效')
        return predictor
    except Exception as exc:raise ValueError('模型包加载或预热失败：'+str(exc)[:350]) from exc

class VersionService:
    def __init__(self,jobs):
        self.jobs=jobs;self.models=jobs.models;self.store=jobs.store;self.root=self.store.root;self.pointer=self.root/'active-model.json'
        with self.store.connect() as db:db.execute('CREATE TABLE IF NOT EXISTS model_versions (version TEXT PRIMARY KEY, document TEXT NOT NULL)')
        if self.pointer.exists():
            selected=json.loads(self.pointer.read_text(encoding='utf-8'))['version']
            if selected!=BASELINE:
                record=self.get(selected);predictor,calibration,path=self.prepare(record)
                with self.models.lock:self.swap(record,predictor,calibration,path)
    def baseline(self):
        entry=json.loads((MODEL_ROOT/'weights/manifest.json').read_text(encoding='utf-8'))['models']['industrial']
        return {'version':BASELINE,'status':'baseline','eligible':True,'sha256':entry['sha256'],'path':str(MODEL_ROOT/entry['path']),'calibration':json.loads((ROOT/'configs/baseline-alarm.json').read_text(encoding='utf-8')),'limitations':'公开基线；历史数据可能已用于该模型训练，不代表独立准确率。'}
    def list(self):
        with self.store.connect() as db:stored={r[0]:json.loads(r[1]) for r in db.execute('SELECT version,document FROM model_versions')}
        result=[self.baseline()]
        for j in self.store.list():
            if j.get('candidate'):
                c=j['candidate'];result.append(stored.pop(c['version'],c))
        result.extend(stored.values());return result
    def get(self,version):
        for record in self.list():
            if record['version']==version:return record
        raise KeyError('模型版本不存在')
    def evaluate(self,version):
        record=self.get(version)
        if version==BASELINE:raise ValueError('公开基线不需要候选验证')
        job=self.store.get(record['job_id'])
        if record.get('validation'):return job
        if job['status']=='evaluating':return job
        if not job.get('candidate'):raise ValueError('尚未产生完整候选权重')
        recipe=json.loads((ROOT/'configs/evaluation-recipe.json').read_text(encoding='utf-8'));recipe_sha=hashlib.sha256(json.dumps(recipe,sort_keys=True).encode()).hexdigest()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','validating','training','evaluating')").fetchone():raise ValueError('已有训练或验证任务正在执行，请等待')
            current=json.loads(db.execute('SELECT document FROM jobs WHERE id=?',(job['job_id'],)).fetchone()[0])
            current.update(status='evaluating',updated_at=time.time(),evaluation_recipe=recipe,evaluation_recipe_sha256=recipe_sha,message='正在启动冻结评估',cancel_requested=False)
            db.execute('UPDATE jobs SET status=?,document=? WHERE id=?',('evaluating',json.dumps(current,ensure_ascii=False),job['job_id']))
        self.models.busy=True
        try:
            with self.models.lock:
                proc=subprocess.Popen([sys.executable,'-m','demo_server.evaluate_worker','--root',str(self.root),'--job',job['job_id']],cwd=ROOT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));self.jobs.processes[job['job_id']]=proc
        except Exception as exc:self.store.patch(job['job_id'],status='failed',error='无法启动验证进程：'+str(exc));raise ValueError(str(exc))
        return current
    def prepare(self,record):
        if not record.get('eligible'):raise ValueError('候选未通过验证，拒绝激活：'+'；'.join(record.get('validation',{}).get('reasons',[])))
        path=Path(record['path'])
        if record['version']!=BASELINE:
            path=(self.root/path).resolve()
            if not path.is_relative_to(self.root.resolve()):raise ValueError('候选路径越界')
        predictor=predictor_from_bundle(path,record['sha256']);calibration=record['calibration']
        if calibration['model_version']!=record['version'] or calibration['channels']!=predictor.channels:raise ValueError('预警校准与候选版本不匹配')
        payload={k:v for k,v in calibration.items() if k!='calibration_id'}
        if hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()!=calibration['calibration_id']:raise ValueError('预警校准完整性不一致')
        return predictor,calibration,path
    def swap(self,record,predictor,calibration,path):
        self.models.predictor=predictor;self.models.version=record['version'];self.models.calibration=calibration;self.models.bundle_path=path;self.models.bundle_sha256=record['sha256']
    def activate(self,version):
        with FileLock(str(self.root/'training.lock'),timeout=0),self.models.lock:
            if any(j['status'] in ACTIVE for j in self.store.list()):raise ValueError('训练或验证期间不能切换模型')
            record=self.get(version);predictor,calibration,path=self.prepare(record)
            previous=self.models.version;tmp=self.pointer.with_suffix('.tmp')
            tmp.write_text(json.dumps({'version':version,'previous_version':previous,'sha256':record['sha256'],'activated_at':time.time()},ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(self.pointer)
            self.swap(record,predictor,calibration,path)
        return {'ready':True,'model_version':version,'previous_version':previous}
